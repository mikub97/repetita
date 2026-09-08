"""
"I already know this."

A claim, not a measurement. It takes a card out of the queue on the learner's
word, and the schema has always distinguished that from a card that earned its
way out over months -- `declared` against `earned`. This tests that the
distinction is kept, in both directions, because it is the whole reason the
button is safe to have: a claim can be revisited, evidence cannot be faked.
"""

import pytest

from repetita import store
from repetita.web.app import create_app

COURSE = "courses/pt-br-from-pl"


@pytest.fixture
def app(tmp_path):
    return create_app(COURSE, db_path=tmp_path / "study.db")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def handles(app):
    return app.extensions["repetita"].handles


@pytest.fixture
def con(app):
    return store.connect(app.config["REPETITA_DB"])


def a_card(client, handles):
    body = client.get("/api/session").get_json()
    card = body["cards"][0]
    return card, handles.card(card["id"])


class TestDeclaring:
    def test_it_takes_the_card_out_of_the_queue(self, client, handles, con):
        card, card_id = a_card(client, handles)
        before = client.get("/api/state").get_json()["owed"]

        result = client.post("/api/known", json={"card_id": card["id"]}).get_json()

        assert result["declared"] is True
        assert store.get_state(con, card_id).retired_at is not None
        assert client.get("/api/state").get_json()["owed"] <= before

    def test_it_is_recorded_as_a_claim_not_as_evidence(self, client, handles, con):
        card, card_id = a_card(client, handles)
        client.post("/api/known", json={"card_id": card["id"]})
        assert store.get_state(con, card_id).retired_reason == "declared"

    def test_it_writes_nothing_to_the_review_log(self, client, handles, con):
        # The log is a record of answers given. Declaring is not an answer, and
        # letting it in would corrupt every accuracy figure computed from it --
        # including the gate that decides how fast new material arrives.
        card, _ = a_card(client, handles)
        before = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        client.post("/api/known", json={"card_id": card["id"]})
        after = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        assert after == before

    def test_the_count_is_visible(self, client, handles):
        # A claim nobody can see is a claim nobody can revisit.
        card, _ = a_card(client, handles)
        assert client.get("/api/state").get_json()["declared"] == 0
        client.post("/api/known", json={"card_id": card["id"]})
        assert client.get("/api/state").get_json()["declared"] == 1

    def test_an_unknown_handle_is_refused(self, client):
        assert client.post("/api/known", json={"card_id": "not-a-handle"}).status_code == 404

    def test_it_speaks_handles_not_card_ids(self, client, handles, con):
        _, card_id = a_card(client, handles)
        assert client.post("/api/known", json={"card_id": card_id}).status_code == 404


class TestUndo:
    def test_the_claim_can_be_taken_back(self, client, handles, con):
        card, card_id = a_card(client, handles)
        client.post("/api/known", json={"card_id": card["id"]})

        result = client.post("/api/known", json={"card_id": card["id"], "undo": True}).get_json()

        assert result["declared"] is False
        assert store.get_state(con, card_id).retired_at is None

    def test_undo_costs_the_card_nothing(self, client, handles, con):
        # "Actually, I don't know it" is not the same as getting it wrong, and
        # must not cost an interval.
        card, card_id = a_card(client, handles)
        client.post("/api/answer", json={"card_id": card["id"], "text": "zzq"})
        before = store.get_state(con, card_id)

        client.post("/api/known", json={"card_id": card["id"]})
        client.post("/api/known", json={"card_id": card["id"], "undo": True})
        after = store.get_state(con, card_id)

        assert (after.due, after.interval, after.seen, after.lapses) == (
            before.due,
            before.interval,
            before.seen,
            before.lapses,
        )

    def test_it_will_not_undo_a_retirement_that_was_earned(self, client, handles, con):
        # Putting an earned retirement back needs the same evidence that took it
        # out. That belongs to a review flow, not to an undo button.
        import dataclasses

        card, card_id = a_card(client, handles)
        cs = store.get_state(con, card_id) or store.CardState(
            card_id=card_id, algo="sm2", algo_version=1, state={}
        )
        store.save_state(
            con, dataclasses.replace(cs, retired_at="2026-01-01", retired_reason="earned")
        )

        client.post("/api/known", json={"card_id": card["id"], "undo": True})

        assert store.get_state(con, card_id).retired_reason == "earned"

    def test_declaring_does_not_relabel_earned_evidence_as_a_claim(self, client, handles, con):
        import dataclasses

        card, card_id = a_card(client, handles)
        cs = store.get_state(con, card_id) or store.CardState(
            card_id=card_id, algo="sm2", algo_version=1, state={}
        )
        store.save_state(
            con, dataclasses.replace(cs, retired_at="2026-01-01", retired_reason="earned")
        )

        client.post("/api/known", json={"card_id": card["id"]})

        assert store.get_state(con, card_id).retired_reason == "earned"
