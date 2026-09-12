"""
"This exercise is wrong."

Reporting is the third thing a learner can do to a card, next to answering it and
declaring it known, and it is the only one that says the *material* is at fault.
These tests pin what follows from that: it never reaches the review log, it
suspends rather than retires, it refuses a reason it does not understand, and it
keeps a copy of the text that provoked it -- because the file is about to change
and the report is the only record of what it used to say.
"""

import dataclasses
import datetime as dt

import pytest

from repetita import store
from repetita.web import create_app

COURSE = "courses/pt-br-from-pl"
DAY = dt.date.today().isoformat()


def _lib(app):
    """The library for the app's default course. The extension is a shelf now."""
    return app.extensions["repetita"].get(app.config["REPETITA_COURSE_ID"])


@pytest.fixture
def app(tmp_path):
    return create_app(COURSE, db_path=tmp_path / "study.db")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def handles(app):
    return _lib(app).handles


@pytest.fixture
def con(app):
    return store.connect(app.config["REPETITA_DB"])


def a_card(client, handles):
    """The public payload the client gets, and the card id it never sees."""
    body = client.get("/api/session").get_json()
    card = body["cards"][0]
    return card, handles.card(card["id"])


def report(client, card, reason="wrong_answer", **extra):
    return client.post(
        "/api/report", json={"card_id": card["id"], "reason": reason, "day": DAY, **extra}
    )


class TestReporting:
    def test_it_takes_the_card_out_of_the_queue(self, client, handles, con):
        card, card_id = a_card(client, handles)
        before = client.get("/api/state").get_json()["owed"]

        result = report(client, card).get_json()

        assert result["reported"] is True
        assert store.get_state(con, card_id).suspended_at is not None
        assert result["owed"] <= before

    def test_it_suspends_rather_than_retires(self, client, handles, con):
        # A broken exercise is not one the learner is done with. Collapsing the
        # two lanes would make a content bug look like progress.
        card, card_id = a_card(client, handles)
        report(client, card, "typo")
        state = store.get_state(con, card_id)
        assert state.suspended_at is not None
        assert state.retired_at is None

    def test_it_writes_nothing_to_the_review_log(self, client, handles, con):
        # The log is a record of answers given. A report is not an answer, and
        # letting it in would corrupt every accuracy figure computed from it --
        # including the gate that decides how fast new material arrives.
        card, _ = a_card(client, handles)
        before = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        report(client, card, "ambiguous")
        after = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        assert after == before

    def test_it_snapshots_the_text_that_provoked_it(self, client, handles, con):
        # The test the feature exists for. `notes` is rebuilt from the course
        # files on every load, so a report that pointed at a row would end up
        # describing whatever was written to fix it.
        card, card_id = a_card(client, handles)
        report(client, card, "typo")

        stored = store.open_reports(con)[0]
        assert stored.card_id == card_id
        assert stored.fields  # the note as authored, by value
        assert stored.origin  # the file to go and edit
        assert stored.note_id == card_id.split("#")[0]

    def test_it_keeps_what_the_learner_typed(self, client, handles, con):
        # For `also_correct` this is the entire report: the word to add to
        # `answers:` is the one that was just rejected.
        card, _ = a_card(client, handles)
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq", "day": DAY})
        report(client, card, "also_correct")
        assert store.open_reports(con)[0].given == "zzq"

    def test_the_optional_note_is_kept(self, client, handles, con):
        card, _ = a_card(client, handles)
        report(client, card, "other", note="the cue points at the wrong sense")
        assert store.open_reports(con)[0].note == "the cue points at the wrong sense"

    def test_the_count_is_visible(self, client, handles):
        # A report nobody can see is a report nobody acts on.
        card, _ = a_card(client, handles)
        assert client.get("/api/state").get_json()["reports_open"] == 0
        report(client, card)
        assert client.get("/api/state").get_json()["reports_open"] == 1


class TestRefusals:
    def test_a_reason_it_does_not_know_is_refused(self, client, handles, con):
        # Never coerced to `other`: a report nobody can act on is worse than no
        # report, because it looks like one.
        card, _ = a_card(client, handles)
        response = report(client, card, "vibes")
        assert response.status_code == 400
        assert response.get_json() == {"error": "bad_reason"}
        assert store.open_report_count(con) == 0

    def test_a_missing_reason_is_refused(self, client, handles):
        card, _ = a_card(client, handles)
        response = client.post("/api/report", json={"card_id": card["id"]})
        assert response.status_code == 400

    def test_an_unknown_handle_is_refused(self, client):
        response = client.post(
            "/api/report", json={"card_id": "not-a-real-handle", "reason": "typo"}
        )
        assert response.status_code == 404
        assert response.get_json() == {"error": "unknown_card"}

    def test_it_speaks_handles_not_card_ids(self, client, handles):
        # ADR-0005: the id is authored from the material and gives away answers.
        _, card_id = a_card(client, handles)
        response = client.post("/api/report", json={"card_id": card_id, "reason": "typo"})
        assert response.status_code == 404


class TestUndo:
    def test_the_report_can_be_withdrawn(self, client, handles, con):
        card, card_id = a_card(client, handles)
        report(client, card)
        result = client.post(
            "/api/report", json={"card_id": card["id"], "undo": True, "day": DAY}
        ).get_json()

        assert result["reported"] is False
        assert result["reports_open"] == 0
        assert store.get_state(con, card_id).suspended_at is None

    def test_undo_costs_the_card_nothing(self, client, handles, con):
        # "Actually that exercise is fine" is not the same as getting it wrong.
        card, card_id = a_card(client, handles)
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq", "day": DAY})
        before = store.get_state(con, card_id)

        report(client, card)
        client.post("/api/report", json={"card_id": card["id"], "undo": True, "day": DAY})
        after = store.get_state(con, card_id)

        assert (after.due, after.interval, after.seen, after.lapses) == (
            before.due,
            before.interval,
            before.seen,
            before.lapses,
        )

    def test_it_leaves_a_suspension_it_did_not_create(self, client, handles, con):
        # The importer carries suspensions across from the predecessor. Reporting
        # such a card and then undoing must put back exactly what the report
        # took -- nothing -- rather than resurrecting material someone had
        # deliberately parked. This is what the `suspended` column is for.
        card, card_id = a_card(client, handles)
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq", "day": DAY})
        state = store.get_state(con, card_id)
        store.save_state(con, dataclasses.replace(state, suspended_at="2026-01-01"))

        report(client, card, "typo")
        assert store.open_reports(con)[0].suspended is False

        client.post("/api/report", json={"card_id": card["id"], "undo": True, "day": DAY})
        assert store.get_state(con, card_id).suspended_at == "2026-01-01"

    def test_undo_with_nothing_to_undo_is_a_no_op(self, client, handles, con):
        # `undo_known` is already silently idempotent; a refusal here would make
        # the two endpoints behave differently for no reason a learner can see.
        card, _ = a_card(client, handles)
        response = client.post(
            "/api/report", json={"card_id": card["id"], "undo": True, "day": DAY}
        )
        assert response.status_code == 200
        assert response.get_json()["reported"] is False
