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

COURSE = Path(__file__).resolve().parents[2] / "courses" / "pt-br-from-pl"


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

    def test_a_preview_says_how_many_never_which(self, client, con, plan):
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "cumprimentos"}]},
        )
        raw = client.post(f"/api/plans/{plan['id']}/preview", json={"budget": 5}).data.decode()
        assert not [cid for cid in self._card_ids(con) if cid in raw]
        assert "picked" in json.loads(raw)


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

    def test_an_answer_records_which_revision_served_it(self, client, con, plan):
        client.put(f"/api/plans/{plan['id']}", json={"active": True})
        card = client.get("/api/session").get_json()["cards"][0]
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq"})
        row = con.execute(
            "SELECT plan_revision_id FROM review_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["plan_revision_id"] is not None


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
