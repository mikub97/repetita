"""
The authoring surface: writing exercises over HTTP.

`/api/material/check` exists so that the browser never has a second opinion
about whether material is servable -- the leak rule is accent-sensitive and
word-boundary aware, and a copy of it in JavaScript would eventually disagree
with the one that decides what gets served. These tests pin what it answers.
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
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def client(course_dir, tmp_path):
    return create_app(course_dir, db_path=tmp_path / "study.db").test_client()


@pytest.fixture
def con(course_dir, tmp_path):
    app = create_app(course_dir, db_path=tmp_path / "study.db")
    return store.connect(app.config["REPETITA_DB"])


def gap(answer, **fields):
    # The prompt deliberately does not contain the answer: an exercise that
    # gives itself away is quarantined, which is a different test.
    return {
        "notetype": "gap",
        "fields": {"prompt": "Eu ___ em Lisboa.", "answers": [answer], **fields},
    }


class TestWhatTheAuthoringScreenIsTold:
    def test_a_notetype_carries_its_cards_not_only_its_fields(self, client):
        # Without these the screen cannot say what an exercise will become, nor
        # which field is the answer.
        shape = client.get("/api/material").get_json()["notetypes"]["vocab"]
        assert set(shape["cards"]) == {"recognize", "produce", "listen"}
        assert shape["cards"]["produce"]["expect"] == "l2"
        assert shape["cards"]["produce"]["grader"] == "typed"

    def test_it_says_which_fields_are_shown_with_the_question(self, client):
        # Composed by `NoteType.visible_before`, not by a list rebuilt in the
        # browser: a card's `ask` fields are shown whatever their own visibility
        # says, and its answer never is.
        card = client.get("/api/material").get_json()["notetypes"]["vocab"]["cards"]["recognize"]
        assert "l2" in card["visible_before"], "the question is asked with it"
        assert "l1" not in card["visible_before"], "that is the answer"

    def test_it_says_which_forms_a_grader_can_mark(self, client):
        cards = client.get("/api/material").get_json()["notetypes"]["phrase"]["cards"]
        assert cards["say"]["askable"] == ["flashcard"], "self-rating, so nothing else"


class TestTheDryRun:
    def test_it_names_the_exercise_before_it_exists(self, client):
        body = client.post(
            "/api/material/check", json={"unit": "nova", "rows": [gap("moro")]}
        ).get_json()
        assert body["rows"][0]["label"] == "moro"
        assert body["rows"][0]["cards"] == ["fill"]

    def test_it_catches_an_answer_given_away_by_a_hint(self, client):
        body = client.post(
            "/api/material/check",
            json={"unit": "nova", "rows": [gap("moro", hint="moro em Lisboa")]},
        ).get_json()
        assert body["rows"][0]["leaks"], "the hint hands over the answer"

    def test_it_says_why_a_form_is_unavailable_rather_than_hiding_it(self, client):
        forms = client.post(
            "/api/material/check", json={"unit": "nova", "rows": [gap("moro")]}
        ).get_json()["rows"][0]["forms"]["fill"]
        assert forms["typein"] is None, "None means available"
        assert "two words" in forms["wordbank"]
        assert "wrong answers" in forms["choice"]
        assert "cannot judge" in forms["flashcard"]

    def test_typed_wrong_answers_make_a_multiple_choice_possible(self, client):
        row = gap("moro", distractors=["moras", "mora"])
        forms = client.post("/api/material/check", json={"unit": "nova", "rows": [row]}).get_json()[
            "rows"
        ][0]["forms"]["fill"]
        assert forms["choice"] is None

    def test_it_writes_nothing(self, client):
        client.post("/api/material/check", json={"unit": "nova", "rows": [gap("moro")]})
        assert {u["id"] for u in client.get("/api/material").get_json()["units"]} == {"01"}

    def test_a_refusal_is_reported_per_row_rather_than_failing_the_batch(self, client):
        # Half a set of exercises is still worth checking, and a screen that
        # goes blank because one row is half-typed is a screen you fight.
        body = client.post(
            "/api/material/check",
            json={"unit": "nova", "rows": [gap("moro"), {"notetype": "quiz", "fields": {}}]},
        ).get_json()
        assert body["rows"][0]["label"] == "moro"
        assert "no 'quiz' exercise" in body["rows"][1]["refused"]


class TestSaving:
    def test_it_creates_the_set_and_serves_it(self, client):
        report = client.post(
            "/api/sets/nova/exercises",
            json={"title": {"en": "New"}, "rows": [gap("moro"), gap("trabalho")]},
        ).get_json()
        assert (report["created"], report["cards_added"]) == (2, 2)

        body = client.get("/api/material").get_json()
        assert {u["id"] for u in body["units"]} == {"01", "nova"}
        assert {n["label"] for n in body["notes"] if n["unit"] == "nova"} == {"moro", "trabalho"}

    def test_saving_again_edits_rather_than_duplicates(self, client):
        ids = client.post("/api/sets/nova/exercises", json={"rows": [gap("moro")]}).get_json()[
            "ids"
        ]
        report = client.post(
            "/api/sets/nova/exercises",
            json={
                "rows": [
                    {
                        "id": ids[0],
                        "notetype": "gap",
                        "fields": {"prompt": "Eu ___ aqui.", "answers": ["morava"]},
                    }
                ]
            },
        ).get_json()
        assert (report["created"], report["updated"]) == (0, 1)
        notes = [n for n in client.get("/api/material").get_json()["notes"] if n["unit"] == "nova"]
        assert len(notes) == 1 and notes[0]["label"] == "morava"

    def test_a_new_exercise_can_be_studied(self, client):
        # The whole point. A set that exists in the database and never reaches a
        # session is a set that was not created.
        client.post("/api/sets/nova/exercises", json={"rows": [gap("moro")]})
        session = client.get("/api/session").get_json()
        assert any("___" in " ".join(c["fields"].values()) for c in session["cards"])

    def test_a_leaking_exercise_is_applied_and_named(self, client):
        # The convention Confirm already uses: applied, then reported. Refusing
        # would strand half-finished work with nowhere to live.
        report = client.post(
            "/api/sets/nova/exercises",
            json={"rows": [gap("moro", hint="moro em Lisboa")]},
        ).get_json()
        assert report["created"] == 1
        assert report["quarantined"] == ["nova.moro"]

    def test_a_form_its_grader_cannot_mark_is_refused(self, client):
        r = client.post(
            "/api/sets/nova/exercises",
            json={"rows": [{**gap("moro"), "forms": {"fill": ["flashcard"]}}]},
        )
        assert r.status_code == 400
        assert "cannot be marked" in r.get_json()["error"]

    def test_an_unknown_type_is_refused_and_nothing_is_written(self, client):
        r = client.post("/api/sets/nova/exercises", json={"rows": [{"notetype": "quiz"}]})
        assert r.status_code == 400
        assert {u["id"] for u in client.get("/api/material").get_json()["units"]} == {"01"}
