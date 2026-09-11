"""
Managing the material.

The test this file exists for is `test_an_open_question_still_never_carries_its
_answer`. The management API returns note ids and every field, answers included
-- it has to, or you could not fix a typo in an answer. That is the carve-out
ADR-0008 records. What must not move is the rule ADR-0005 is actually about:
while a question is open, the answer is not in the page.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.web.app import create_app

COURSE = """\
format_version: 1
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
id: t
"""

NOTES = """\
    notetype: vocab
    tags: [A2, comida]
    notes:
      - id: feira
        l2: a feira
        l1: targ
      - id: rua
        l2: a rua
        l1: ulica
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "course"
    for unit in ("01", "02"):
        (root / "units" / unit / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def app(course_dir, tmp_path):
    return create_app(course_dir, db_path=tmp_path / "study.db")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def con(app):
    return store.connect(app.config["REPETITA_DB"])


class TestReadingTheMaterial:
    def test_it_returns_every_unit_and_note(self, client):
        body = client.get("/api/material").get_json()
        assert {u["id"] for u in body["units"]} == {"01", "02"}
        assert {n["id"] for n in body["notes"]} == {"feira", "rua"}

    def test_it_returns_the_answers(self, client):
        # The carve-out, asserted rather than assumed: you cannot fix a typo in
        # an answer you cannot see.
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["fields"]["l2"] == "a feira"

    def test_it_describes_each_field_so_the_editor_can_warn(self, client):
        # Which fields are shown while the question is open is the difference
        # between a hint and a giveaway.
        shape = client.get("/api/material").get_json()["notetypes"]["vocab"]["fields"]
        assert shape["l2"]["visibility"] == "after"
        assert shape["l1"]["visibility"] == "before"


class TestStagingAndConfirming:
    def test_nothing_changes_until_confirm(self, client):
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"explain": "Mercado."}},
        )
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert "explain" not in note["fields"]

    def test_the_pending_list_shows_before_and_after(self, client):
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "tags", "payload": ["A2", "cidade"]},
        )
        change = client.get("/api/material/pending").get_json()["changes"][0]
        assert change["before"] == ["A2", "comida"]
        assert change["after"] == ["A2", "cidade"]

    def test_confirm_applies_and_the_change_is_served(self, client):
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"explain": "Mercado."}},
        )
        client.post("/api/material/confirm")
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["fields"]["explain"] == "Mercado."

    def test_discard_leaves_the_material_alone(self, client):
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"explain": "no"}},
        )
        client.post("/api/material/discard", json={})
        assert client.get("/api/material/pending").get_json()["changes"] == []

    def test_moving_a_note_between_sets(self, client):
        client.post(
            "/api/material/stage", json={"note_id": "feira", "kind": "unit", "payload": "02"}
        )
        client.post("/api/material/confirm")
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["unit"] == "02"

    def test_moving_a_note_costs_it_no_history(self, client, con):
        import datetime as dt

        from repetita import srs
        from repetita.core.types import Rating

        store.record_answer(
            con,
            "feira#produce",
            Rating.GOOD,
            backend=srs.get("sm2"),
            at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        )
        before = store.get_state(con, "feira#produce")

        client.post(
            "/api/material/stage", json={"note_id": "feira", "kind": "unit", "payload": "02"}
        )
        client.post("/api/material/confirm")

        after = store.get_state(con, "feira#produce")
        assert (after.due, after.interval, after.seen) == (before.due, before.interval, before.seen)

    def test_removing_a_note_archives_it_and_keeps_its_history(self, client, con):
        client.post(
            "/api/material/stage", json={"note_id": "rua", "kind": "archive", "payload": True}
        )
        client.post("/api/material/confirm")

        assert "rua" not in {n["id"] for n in client.get("/api/material").get_json()["notes"]}
        row = con.execute("SELECT archived_at FROM notes WHERE id = 'rua'").fetchone()
        assert row is not None and row["archived_at"] is not None


class TestRefusals:
    def test_a_note_id_cannot_be_changed(self, client):
        # Rule 1. The one mistake here that cannot be undone.
        r = client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"id": "mercado"}},
        )
        assert r.status_code == 400

    def test_a_notetype_cannot_be_changed(self, client):
        r = client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"notetype": "gap"}},
        )
        assert r.status_code == 400

    def test_an_unknown_kind_is_refused(self, client):
        r = client.post("/api/material/stage", json={"note_id": "feira", "kind": "vibes"})
        assert r.status_code == 400


class TestTheQuarantineIsReported:
    def test_an_edit_that_gives_away_the_answer_is_named_not_hidden(self, client):
        # Applied, but not servable. A note that silently stops appearing is
        # worse than one that visibly cannot be used.
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"example_l1": "a feira"}},
        )
        report = client.post("/api/material/confirm").get_json()
        assert "feira" in report["quarantined"]


class TestImport:
    def _edit(self, client, note_id, value):
        client.post(
            "/api/material/stage",
            json={"note_id": note_id, "kind": "fields", "payload": {"l1": value}},
        )
        client.post("/api/material/confirm")

    def test_a_clean_import_reports_no_conflicts(self, client):
        assert client.post("/api/import/preview").get_json()["conflicts"] == []

    def test_a_clash_shows_both_versions(self, client, course_dir):
        self._edit(client, "feira", "targ (rynek)")
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(path.read_text().replace("l1: targ\n", "l1: targowisko\n"))

        conflicts = client.post("/api/import/preview").get_json()["conflicts"]

        assert [c["note_id"] for c in conflicts] == ["feira"]
        assert conflicts[0]["file"]["l1"] == "targowisko"
        assert conflicts[0]["mine"]["l1"] == "targ (rynek)"

    def test_preview_writes_nothing(self, client, course_dir):
        self._edit(client, "feira", "targ (rynek)")
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(path.read_text().replace("l1: targ\n", "l1: targowisko\n"))

        client.post("/api/import/preview")

        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["fields"]["l1"] == "targ (rynek)"

    def test_a_note_not_named_keeps_the_version_you_have(self, client, course_dir):
        # The default has to be this way round. An import that overwrote work by
        # omission is the one outcome a confirmation step exists to prevent.
        self._edit(client, "feira", "targ (rynek)")
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(path.read_text().replace("l1: targ\n", "l1: targowisko\n"))

        report = client.post("/api/import/apply", json={}).get_json()

        assert report["kept_mine"] == ["feira"]
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["fields"]["l1"] == "targ (rynek)"

    def test_taking_the_file_replaces_it_and_settles(self, client, course_dir):
        self._edit(client, "feira", "targ (rynek)")
        path = course_dir / "units" / "01" / "notes" / "n.yaml"
        path.write_text(path.read_text().replace("l1: targ\n", "l1: targowisko\n"))

        client.post("/api/import/apply", json={"take_file": ["feira"]})

        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["fields"]["l1"] == "targowisko"
        # And it does not come back as a conflict forever after.
        assert client.post("/api/import/preview").get_json()["conflicts"] == []


class TestTheLeakRuleHolds:
    def test_an_open_question_still_never_carries_its_answer(self, client, con):
        # The management API sees everything. The session must not.
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"explain": "Mercado de rua."}},
        )
        client.post("/api/material/confirm")

        raw = client.get("/api/session").data.decode()

        # `l2` is the answer to `feira#recognize`, which is in this session.
        assert "a feira" not in raw or "recognize" not in raw

    def test_the_session_still_addresses_cards_by_handle(self, client, con):
        # Asserted on the id field rather than on raw bytes, because in this
        # course the note ids *are* the words -- `feira` appears in the payload
        # as content, which is exactly the situation ADR-0005 was written for and
        # exactly why a substring search cannot tell a leak from a coincidence.
        # `tests/web/test_api.py` does the raw-bytes check against the real
        # course, where the ids are distinctive enough for it to mean something.
        served = client.get("/api/session").get_json()["cards"]
        known = {r["id"] for r in con.execute("SELECT id FROM cards")}
        assert served
        for card in served:
            assert card["id"] not in known, "a card id reached the client"
            assert len(card["id"]) == 12, "handles are 12 url-safe characters"


class TestTheSeamsTheReviewFound:
    """
    Every one of these passed the suite while broken.

    They all live where two correct pieces meet -- an edit and the reload that
    follows it, a preview and the thing it previews -- which is exactly the
    place unit tests do not look.
    """

    def test_a_card_an_edit_created_survives_the_reload(self, client, con):
        # `_reload_library()` runs inside the same Confirm request, and used to
        # recompute this note's cards from the *file*, which has no audio. The
        # card was created and destroyed without either showing up.
        client.post(
            "/api/material/stage",
            json={"note_id": "feira", "kind": "fields", "payload": {"audio": "feira.mp3"}},
        )
        report = client.post("/api/material/confirm").get_json()
        assert report["cards_added"] == 1

        live = {r["id"] for r in con.execute("SELECT id FROM cards WHERE archived_at IS NULL")}
        assert "feira#listen" in live, "the reload archived the card the edit had just made"
        assert "feira#listen" in set(store.card_ids(con))

    def test_an_archived_notes_cards_do_not_come_back(self, client, con):
        # The file still has the note, so the file expansion used to upsert its
        # cards with archived_at = NULL -- leaving cards live whose note is gone.
        # `/api/session` drops them silently; `owed_count()` counts them forever.
        client.post(
            "/api/material/stage", json={"note_id": "rua", "kind": "archive", "payload": True}
        )
        client.post("/api/material/confirm")
        client.post("/api/import/apply", json={})

        live = {r["id"] for r in con.execute("SELECT id FROM cards WHERE archived_at IS NULL")}
        assert not [c for c in live if c.startswith("rua#")]

    def test_a_null_payload_is_refused_and_leaves_the_tab_usable(self, client):
        # Accepted, it wedged the tab permanently: pending and confirm both 500
        # from then on, and Discard lives inside the drawer pending draws.
        assert (
            client.post(
                "/api/material/stage", json={"note_id": "feira", "kind": "fields", "payload": None}
            ).status_code
            == 400
        )
        assert client.get("/api/material/pending").status_code == 200
        assert client.post("/api/material/confirm").status_code == 200

    def test_tags_and_unit_payloads_are_checked_too(self, client):
        for kind, payload in (("tags", "comida"), ("unit", ""), ("unit", None)):
            r = client.post(
                "/api/material/stage", json={"note_id": "feira", "kind": kind, "payload": payload}
            )
            assert r.status_code == 400, f"{kind}={payload!r} should be refused"

    def test_a_warning_is_not_reported_as_a_leak(self, client, con):
        # A servable note used to get a red border and a warning triangle for a
        # non-fatal problem, which teaches people to ignore the real ones.
        con.execute(
            "UPDATE notes SET fields = ?, edited_at = ? WHERE id = 'feira'",
            ('{"l2": "a feira", "l1": "targ", "example_l1": "no mercado"}', "2026-09-11T00:00:00Z"),
        )
        con.commit()
        note = next(
            n for n in client.get("/api/material").get_json()["notes"] if n["id"] == "feira"
        )
        assert note["leaks"] == []
        assert "warnings" in note

    def test_an_unknown_plan_is_refused_rather_than_ignored(self, client):
        # It used to serve the plain session and file the answers with no
        # revision -- so the one request that could tell you the plan was gone
        # was the one that said nothing.
        assert client.get("/api/session?plan=999").status_code == 404

    def test_a_note_that_comes_back_is_previewed_before_it_happens(self, client, con):
        # Unchanged by hash, so the preview said "nothing will happen" and the
        # import then restored it -- the disagreement a preview exists to stop.
        con.execute("UPDATE notes SET archived_at = '2026-09-11' WHERE id = 'rua'")
        con.commit()

        preview = client.post("/api/import/preview").get_json()

        assert preview["restored"] == 1
