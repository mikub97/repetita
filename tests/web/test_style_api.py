"""
"Jak się uczę": the Study tab's own settings.

The tests that carry weight here are the three at the bottom. A named plan must
still win over a saved style, or ADR-0007's "a session is built under a plan only
when the request names one" is gone by accident. An answer must record one
revision or the other and never both, or every later comparison is wrong. And no
payload may carry a card id, which is ADR-0005 and is asserted over raw bytes
because a parsed assertion only checks the places you thought to look.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repetita import store
from repetita.store import styles as S
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


class TestReadingAStyle:
    def test_a_fresh_account_gets_the_default(self, client):
        body = client.get("/api/style").get_json()
        assert body["style"] == body["default"]
        assert body["style"]["introductions"] == "lesson"

    def test_it_offers_the_named_modes(self, client):
        keys = {m["key"] for m in client.get("/api/style").get_json()["modes"]}
        assert {"kurs", "nadrabianie", "spokojny", "sciezka", "intensywnie"} <= keys

    def test_it_offers_only_axes_the_course_puts_an_order_on(self, client):
        # `topic` is a set of names with no sequence. Offering it would make
        # "easiest first" mean "alphabetically first", which is the bug this
        # whole change began with, reintroduced one level up.
        axes = client.get("/api/style").get_json()["axes"]
        assert "topic" not in axes

    def test_it_reports_the_debt_and_the_forecast(self, client):
        body = client.get("/api/style").get_json()
        assert isinstance(body["owed"], int)
        assert len(body["forecast"]) == 14


class TestSavingAStyle:
    def test_a_mode_round_trips(self, client):
        client.put("/api/style", json={"mode": "sciezka"})
        style = client.get("/api/style").get_json()["style"]
        assert style["mode"] == "sciezka"
        assert style["introductions"] == "course"

    def test_touching_a_knob_stops_it_claiming_the_preset_name(self, client):
        # An edited preset is not the preset any more, exactly as an edited
        # theme is not. The name must never claim more than it delivers.
        client.put("/api/style", json={"mode": "spokojny"})
        client.put("/api/style", json={"knobs": {"batch": 77}})
        assert client.get("/api/style").get_json()["style"]["mode"] == S.CUSTOM

    def test_a_knob_set_back_to_null_is_removed(self, client):
        client.put("/api/style", json={"knobs": {"batch": 77}})
        client.put("/api/style", json={"knobs": {"batch": None}})
        assert "batch" not in client.get("/api/style").get_json()["style"]["knobs"]

    def test_an_unknown_ordering_is_refused_with_a_sentence(self, client):
        r = client.put("/api/style", json={"introductions": "nonsense"})
        assert r.status_code == 400
        assert "unknown ordering" in r.get_json()["error"]

    def test_an_axis_this_course_does_not_order_is_refused(self, client):
        r = client.put("/api/style", json={"introductions": "axis", "intro_axis": "topic"})
        assert r.status_code == 400
        assert "ordered axis" in r.get_json()["error"]

    def test_an_unknown_knob_is_refused(self, client):
        assert client.put("/api/style", json={"knobs": {"nonsense": 1}}).status_code == 400

    def test_every_save_writes_a_revision(self, client, con):
        def count():
            return con.execute("SELECT COUNT(*) AS n FROM style_revisions").fetchone()["n"]

        before = count()
        client.put("/api/style", json={"mode": "spokojny"})
        client.put("/api/style", json={"mode": "spokojny"})
        # Including the save that changes nothing: a revision records when a
        # setting was in force, not a diff, and a gap in that sequence is the
        # thing ADR-0003 exists to prevent.
        assert count() == before + 2


class TestPreview:
    def test_it_answers_without_saving(self, client):
        r = client.post("/api/style/preview", json={"mode": "intensywnie", "budget": 5})
        assert r.status_code == 200
        assert r.get_json()["picked"] <= 5
        assert client.get("/api/style").get_json()["style"]["mode"] == "kurs"

    def test_a_style_it_cannot_honour_is_refused(self, client):
        r = client.post("/api/style/preview", json={"introductions": "axis", "intro_axis": "topic"})
        assert r.status_code == 400


class TestTheThingsThatMustNotBreak:
    def test_a_named_plan_still_wins(self, client):
        # ADR-0007: a session is built under a plan only when the request names
        # one. A saved style must not quietly become the plan's replacement, and
        # must not quietly override a plan either.
        plan = client.post("/api/plans", json={"name": "p", "active": True}).get_json()
        client.put(
            f"/api/plans/{plan['id']}",
            json={"priorities": [{"axis": "topic", "value": "x"}], "knobs": {"batch": 3}},
        )
        client.put("/api/style", json={"knobs": {"batch": 40}})
        under_plan = client.get(f"/api/session?plan={plan['id']}").get_json()
        assert len(under_plan["cards"]) <= 3

    def test_the_session_follows_the_saved_style(self, client):
        client.put("/api/style", json={"knobs": {"batch": 2}})
        assert len(client.get("/api/session").get_json()["cards"]) <= 2

    def test_style_none_builds_the_default(self, client):
        client.put("/api/style", json={"knobs": {"batch": 2}})
        plain = client.get("/api/session?style=none").get_json()
        assert len(plain["cards"]) > 2

    def test_a_study_tab_answer_records_a_style_revision_and_no_plan_revision(self, client, con):
        client.put("/api/style", json={"mode": "spokojny"})
        card = client.get("/api/session").get_json()["cards"][0]
        client.post("/api/answer", json={"card_id": card["id"], "text": "whatever"})
        row = con.execute(
            "SELECT plan_revision_id, style_revision_id FROM review_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["plan_revision_id"] is None
        assert row["style_revision_id"] is not None

    def test_a_plan_answer_records_a_plan_revision_and_no_style_revision(self, client, con):
        client.put("/api/style", json={"mode": "spokojny"})
        plan = client.post("/api/plans", json={"name": "p", "active": True}).get_json()
        card = client.get(f"/api/session?plan={plan['id']}").get_json()["cards"][0]
        client.post(
            "/api/answer", json={"card_id": card["id"], "text": "whatever", "plan": plan["id"]}
        )
        row = con.execute(
            "SELECT plan_revision_id, style_revision_id FROM review_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["plan_revision_id"] is not None
        assert row["style_revision_id"] is None

    def test_no_style_payload_carries_a_card_id(self, client, con):
        # Over raw bytes, not a parsed field: ADR-0005 says anything that carries
        # a card id to the client reopens the leak, and a quarter of this corpus
        # has an id that IS the answer.
        ids = [r["id"] for r in con.execute("SELECT id FROM cards")]
        client.put("/api/style", json={"mode": "intensywnie"})
        blobs = [
            client.get("/api/style").data,
            client.post("/api/style/preview", json={"budget": 20}).data,
        ]
        for blob in blobs:
            text = blob.decode()
            for card_id in ids:
                assert card_id not in text, f"{card_id} reached the client"
                assert json.dumps(card_id)[1:-1] not in text
