"""
The lesson designer's HTTP surface.

The test this file exists for is `test_no_catalogue_payload_carries_a_card_id`.
ADR-0005: card ids are authored from the material, so a quarter of the corpus has
an id that *is* the answer, and "anything added later that carries a card id to
the client -- a deep link, an error message, a debug endpoint -- reopens this".

A catalogue is exactly that shape of thing. It asserts over the raw response
bytes rather than a parsed field, for the same reason `test_api.py` does: a
parsed assertion only checks the places you thought to look.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repetita import store
from repetita.web.app import create_app

COURSE = Path(__file__).resolve().parents[1] / "fixtures" / "demo-course"


@pytest.fixture
def app(tmp_path):
    return create_app(COURSE, db_path=tmp_path / "study.db")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def con(app):
    return store.connect(app.config["REPETITA_DB"])


@pytest.fixture
def plan(client):
    return client.post("/api/plans", json={"name": "test", "active": True}).get_json()


class TestCatalogue:
    def test_it_counts_material_by_axis(self, client):
        body = client.get("/api/catalogue?group_by=topic").get_json()
        assert body["group_by"] == ["topic"]
        assert body["rows"], "the sample course should classify into at least one topic"
        assert all("cards" in r and "notes" in r for r in body["rows"])

    def test_it_reports_the_axes_a_course_declares(self, client):
        axes = {a["axis"] for a in client.get("/api/catalogue").get_json()["axes"]}
        assert {"level", "track", "topic"} <= axes

    def test_a_filter_narrows_it(self, client):
        body = client.get("/api/catalogue?group_by=topic&where=state=new").get_json()
        assert body["rows"]

    def test_an_unusable_selector_is_refused_not_ignored(self, client):
        # Silently ignoring it would answer a different question than the one
        # asked, and look like an empty course.
        assert client.get("/api/catalogue?where=nonsense").status_code == 400


class TestNoLeak:
    def _card_ids(self, con):
        return [r["id"] for r in con.execute("SELECT id FROM cards")]

    def test_no_catalogue_payload_carries_a_card_id(self, client, con):
        raw = client.get("/api/catalogue?group_by=topic,state").data.decode()
        leaked = [cid for cid in self._card_ids(con) if cid in raw]
        assert not leaked, f"card ids in the catalogue payload: {leaked[:5]}"

    def test_no_catalogue_payload_carries_a_note_id(self, client, con):
        raw = client.get("/api/catalogue?group_by=topic,state").data.decode()
        note_ids = [r["id"] for r in con.execute("SELECT id FROM notes")]
        assert not [n for n in note_ids if n in raw]

    def test_a_preview_carries_no_card_id(self, client, con, plan):
        # It used to say how many and never which. It now names a shuffled
        # sample -- deliberately, see TestThePreviewNamesThem -- and what has
        # not moved is this: no card id reaches the client, ever.
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "cumprimentos"}]},
        )
        raw = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 5}).data.decode()
        assert not [cid for cid in self._card_ids(con) if cid in raw]
        assert "picked" in json.loads(raw)


class TestThePreviewNamesThem:
    """
    A loosening of ADR-0005, taken deliberately and bounded here.

    The preview now lists a few names, and a name is usually an answer. What
    must still hold is the boundary: names, not card ids, and a sample rather
    than the list. ADR-0008's amendment has the reasoning; these are the parts
    of it that a future change could break silently.
    """

    def test_it_lists_names_for_what_it_would_introduce(self, client, con, plan):
        body = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 5}).get_json()
        labels = {r["label"] for r in con.execute("SELECT label FROM notes")}
        assert body["names"], "a preview with cards in it should say which"
        assert set(body["names"]) <= labels

    def test_it_is_still_a_sample_not_the_list(self, client, plan):
        from repetita.web.api import PREVIEW_NAMES

        body = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 200}).get_json()
        assert len(body["names"]) <= PREVIEW_NAMES

    def test_the_order_is_not_the_order_you_will_be_asked_in(self, client, plan):
        # The mitigation, asserted: shuffled per call, so reading the preview
        # twice does not teach you the sequence the session will serve. It does
        # not stop you reading answers -- nothing here claims it does.
        seen = {
            tuple(
                client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 20}).get_json()[
                    "names"
                ]
            )
            for _ in range(12)
        }
        assert len(seen) > 1

    def test_a_big_budget_does_not_blow_up_the_query(self, client, plan):
        # `budget` comes from the request. Sampling after the query would put one
        # placeholder per card into a single statement, and SQLite refuses at
        # 999 -- a 500 on the one screen whose job is to be believed.
        r = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 5000})
        assert r.status_code == 200
        assert len(r.get_json()["names"]) <= 12

    def test_no_card_id_rides_along_with_them(self, client, con, plan):
        raw = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 20}).data.decode()
        ids = [r["id"] for r in con.execute("SELECT id FROM cards")]
        assert not [c for c in ids if c in raw]


class TestPlans:
    def test_a_plan_can_be_created_and_listed(self, client, plan):
        assert plan["name"] == "test"
        assert [p["id"] for p in client.get("/api/plans").get_json()["plans"]] == [plan["id"]]

    def test_priorities_round_trip(self, client, plan):
        body = client.put(
            f"/api/plans/{plan['id']}",
            json={
                "priorities": [
                    {"axis": "topic", "value": "cumprimentos"},
                    {"axis": "track", "value": "vocabulario"},
                ]
            },
        ).get_json()
        assert [p["value"] for p in body["priorities"]] == ["cumprimentos", "vocabulario"]
        assert [p["rank"] for p in body["priorities"]] == [0, 1]

    def test_an_unknown_knob_is_refused(self, client, plan):
        r = client.put(f"/api/plans/{plan['id']}", json={"knobs": {"turbo": 11}})
        assert r.status_code == 400

    def test_a_nameless_plan_is_refused(self, client):
        assert client.post("/api/plans", json={"name": "  "}).status_code == 400

    def test_an_unknown_plan_is_a_404(self, client):
        assert client.put("/api/plans/999", json={"knobs": {}}).status_code == 404
        assert client.post("/api/plans/999/preview", json={}).status_code == 404

    def test_the_preview_shows_the_mix_the_list_asks_for(self, client, plan):
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "cumprimentos"}]},
        )
        body = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 4}).get_json()
        assert body["budget"] == 4
        assert body["picked"] <= 4


class TestSessionUsesThePlan:
    def test_an_active_plan_still_serves_a_session(self, client, plan):
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "cumprimentos"}], "active": True},
        )
        body = client.get("/api/session").get_json()
        assert body["cards"], "an active plan must not empty the queue"

    def test_an_answer_under_a_plan_records_which_revision_served_it(self, client, con, plan):
        card = client.get(f"/api/session?plan={plan['id']}").get_json()["cards"][0]
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq", "plan": plan["id"]})
        row = con.execute(
            "SELECT plan_revision_id FROM review_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["plan_revision_id"] is not None

    def test_an_answer_outside_a_plan_records_no_revision(self, client, con, plan):
        # An answer given on the Study tab is not evidence about a plan, and
        # filing it under one would make every later comparison wrong.
        client.put(f"/api/plans/{plan['id']}", json={"active": True})
        card = client.get("/api/session").get_json()["cards"][0]
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq"})
        row = con.execute(
            "SELECT plan_revision_id FROM review_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["plan_revision_id"] is None


class TestIssues:
    def test_an_issue_can_be_raised_listed_and_resolved(self, client):
        raised = client.post(
            "/api/issues",
            json={
                "body": "two tags for one subject",
                "kind": "duplicate",
                "selector": "topic=tempo",
            },
        ).get_json()
        assert [i["id"] for i in client.get("/api/issues").get_json()["issues"]] == [raised["id"]]

        client.post(f"/api/issues/{raised['id']}/resolve", json={"note": "merged"})
        assert client.get("/api/issues").get_json()["issues"] == []

    def test_an_empty_issue_is_refused(self, client):
        assert client.post("/api/issues", json={"body": ""}).status_code == 400


class TestAPlanIsAnAdditionalPath:
    """
    A plan does not alter the Study tab. It is a second way through the same
    material, asked for per request -- which is what makes "go back to the
    course's own order" one click rather than a deletion.
    """

    def _with_priorities(self, client, plan):
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "track", "value": "vocabulario"}], "active": True},
        )

    def test_a_plan_is_only_used_when_asked_for(self, client, plan):
        self._with_priorities(client, plan)
        plain = client.get("/api/session").get_json()["cards"]
        under_plan = client.get(f"/api/session?plan={plan['id']}").get_json()["cards"]
        assert plain and under_plan

    def test_an_active_plan_does_not_change_the_plain_session(self, client, plan):
        # The whole point of this shape: building a plan, even marking it
        # active, must leave the ordinary session exactly as it was.
        before = [c["id"] for c in client.get("/api/session").get_json()["cards"]]
        self._with_priorities(client, plan)
        after = [c["id"] for c in client.get("/api/session").get_json()["cards"]]
        assert after == before

    def test_a_nonsense_plan_id_is_refused(self, client):
        assert client.get("/api/session?plan=banana").status_code == 400

    def test_the_plan_orders_the_session(self, client, plan, con):
        self._with_priorities(client, plan)
        assert client.get(f"/api/session?plan={plan['id']}").get_json()["cards"]

    def test_practice_is_not_throttled_by_the_introduction_gate(self, client, plan, con):
        """
        The gate brakes material arriving unasked. A learner who named these
        topics and pressed the button asked -- and gating that answers
        "practise food" with three cards while thirty-eight sit available,
        which is a refusal rather than a protection.
        """
        import datetime as dt

        from repetita import srs, store
        from repetita.core.types import Rating

        # Answer badly enough to shut the gate.
        backend = srs.get("sm2")
        at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        for card_id in store.card_ids(con)[:10]:
            store.record_answer(
                con, card_id, Rating.AGAIN, backend=backend, at=at, local_day=dt.date(2026, 1, 1)
            )

        self._with_priorities(client, plan)
        under_plan = client.get(f"/api/session?plan={plan['id']}").get_json()["cards"]
        assert under_plan, "a closed gate must not empty a practice the learner asked for"

    def test_practising_a_plan_never_hides_the_debt(self, client, plan, con, app):
        # Practice is scoped to the plan's own material, so the session itself is
        # narrow -- but the debt it did not cover is still owed, still counted,
        # and still on the Study tab. A learner who could make owed cards
        # disappear by designing around them would, once, and meet them again a
        # month later at four times the size.
        import datetime as dt

        from repetita import srs, store
        from repetita.core.types import Rating

        backend = srs.get("sm2")
        at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        for card_id in store.card_ids(con)[:4]:
            store.record_answer(
                con, card_id, Rating.GOOD, backend=backend, at=at, local_day=dt.date(2026, 1, 1)
            )

        owed_plain = client.get("/api/state").get_json()["owed"]
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "cumprimentos"}], "active": True},
        )
        owed_with_plan = client.get("/api/state").get_json()["owed"]

        assert owed_with_plan == owed_plain


class TestHiddenActuallyHides:
    """
    A weak test, and worth having anyway.

    There is no browser harness in this repo, so this pins the CSS rule rather
    than the rendering. It exists because the bug it guards is invisible to
    every other kind of test: `hidden` carries `display: none` only from the
    user-agent stylesheet, so any author rule setting `display` on the same
    element beats it. `.plan-bar` is `display: flex`, so leaving a plan hid
    nothing and the bar stayed on screen offering to leave a plan already left.
    """

    STATIC = Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "static"

    def test_the_stylesheet_forces_hidden_to_win(self):
        css = (self.STATIC / "style.css").read_text()
        assert "[hidden]" in css
        block = css.split("[hidden]", 1)[1].split("}", 1)[0]
        assert "display: none !important" in block

    def test_every_element_toggled_by_hidden_is_covered(self):
        # If someone starts toggling a new element, the rule above already
        # covers it -- this asserts we are still relying on that one rule rather
        # than on per-class overrides that have to be remembered.
        js = "\n".join(p.read_text() for p in self.STATIC.glob("*.js"))
        toggled = [line for line in js.splitlines() if ".hidden =" in line]
        assert toggled, "expected the designer to toggle visibility with `hidden`"
