"""
The HTTP layer.

The test this file exists for is `test_open_question_never_carries_its_answer`.
It asserts over the raw response body rather than a parsed field, because a
parsed assertion only checks the places you thought to look -- and the leaks that
have actually happened in this project's predecessor were in the places nobody
thought to look. Bytes on the wire is the only formulation of the rule that a
future field cannot quietly escape.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

from repetita.content.loader import load_course
from repetita.content.notetypes import BUILTIN
from repetita.content.validate import ANSWER_BEARING
from repetita.graders.text import strip_accents
from repetita.store import connect, save_state
from repetita.store.cards import CardState
from repetita.web import create_app
from repetita.web.serialize import shuffled

COURSE = Path(__file__).resolve().parents[2] / "courses" / "pt-br-from-pl"

COURSE_YAML = {
    "format_version": 1,
    "id": "test-course",
    "title": {"en": "Test"},
    "l2": {"code": "xx"},
    "l1": {"code": "yy"},
    "license": {"name": "CC BY-SA 4.0"},
    "scheduler": "sm2",
}


def _sentinels(notetype: str) -> dict[str, object]:
    """
    A distinct nonsense value for every field of a note type.

    Distinct and mutually non-substring, so that finding one in a payload can
    only mean that field was served -- and so the leak validator, which
    quarantines a note whose answer shows up in a visible field, has nothing to
    catch. List fields get two words so the word-bank path is exercised too.
    """
    pool = (f"zzq{c}" for c in "abcdefghijklmnopqrstuvwxyz")
    fields: dict[str, object] = {}
    for name, spec in BUILTIN[notetype].fields.items():
        # `options` carries shape rules of its own (exactly one must be the
        # answer), which would defeat the point of unique sentinels.
        if name == "options":
            continue
        fields[name] = [f"{next(pool)} {next(pool)}"] if spec.type == "text_list" else next(pool)
    return fields


def _write_course(root: Path, notetype: str, fields: dict[str, object]) -> Path:
    notes_dir = root / "units" / "u1" / "notes"
    notes_dir.mkdir(parents=True)
    (root / "course.yaml").write_text(yaml.safe_dump(COURSE_YAML), encoding="utf-8")
    (notes_dir / "n.yaml").write_text(
        yaml.safe_dump({"notetype": notetype, "notes": [{"id": "n1", **fields}]}),
        encoding="utf-8",
    )
    return root


def _app(course_dir: Path, tmp_path: Path):
    return create_app(course_dir, db_path=tmp_path / "study.db")


def _suspend_all_but(db: Path, keep: str, card_ids: list[str]) -> None:
    """
    Leave exactly one card in the queue.

    Without this the raw-body assertion could not be made at all: a `vocab` note
    yields `recognize` and `produce`, and one card's answer is the other card's
    question. Both in one payload is correct behaviour that a substring check
    cannot tell apart from a leak, so the leak test looks at one card at a time.
    """
    con = connect(db)
    try:
        for card_id in card_ids:
            if card_id != keep:
                save_state(
                    con,
                    CardState(
                        card_id=card_id,
                        algo="sm2",
                        algo_version=1,
                        state={},
                        suspended_at="2020-01-01",
                    ),
                )
    finally:
        con.close()


CARDS = [(name, tpl) for name, nt in BUILTIN.items() for tpl in nt.cards]


@pytest.mark.parametrize(("notetype", "template"), CARDS, ids=lambda v: str(v))
def test_open_question_never_carries_its_answer(notetype, template, tmp_path, handles):
    """No response for an open question contains that question's answer."""
    course = _write_course(tmp_path / "course", notetype, _sentinels(notetype))
    result = load_course(course)
    assert not result.fatal, [str(p) for p in result.fatal]

    card_id = f"n1#{template}"
    assert card_id in {c.id for c in result.cards}, "the fixture did not generate the card"

    app = _app(course, tmp_path)
    _suspend_all_but(tmp_path / "study.db", card_id, [c.id for c in result.cards])

    note = result.notes[0]
    expected = note.answers(BUILTIN[notetype].cards[template].expect)
    assert expected, "the fixture note has no answer to leak"

    client = app.test_client()
    for path in ("/api/session", "/api/state"):
        raw = client.get(path).data
        for answer in expected:
            assert answer.encode() not in raw, f"{path} leaked the answer to {card_id}"

    # Guard against a vacuous pass: a payload with no card in it leaks nothing.
    # The served id is an opaque handle, so resolve it to check the right card
    # was actually in the body.
    body = client.get("/api/session").get_json()
    handles = app.extensions["repetita"].handles
    assert [handles.card(c["id"]) for c in body["cards"]] == [card_id]

    # And the id itself is not the card id: card ids are authored from the
    # material, so `obrigado#produce` would carry its own answer.
    assert [c["id"] for c in body["cards"]] != [card_id]


def test_visible_before_never_exposes_an_answer_bearing_field():
    """
    The serialiser trusts `visible_before` completely and adds no filter of its
    own -- two filters for one rule is how they come to disagree. So the trust
    is checked here instead: no built-in note type may put `options` or
    `distractors`, which exist to contain the answer, in the visible set.
    """
    for name, notetype in BUILTIN.items():
        for template in notetype.cards:
            exposed = ANSWER_BEARING & set(notetype.visible_before(template))
            assert not exposed, f"{name}.{template} would serve {exposed}"


def test_shuffled_never_returns_the_original_order():
    """A word bank handed back in the right order is not an exercise."""
    for size in range(2, 7):
        items = [f"w{i}" for i in range(size)]
        for seed in range(50):
            assert shuffled(items, random.Random(seed)) != items
    assert shuffled(["only"], random.Random(0)) == ["only"]
    assert shuffled([], random.Random(0)) == []


# --- the real course ------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    return _app(COURSE, tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def handles(app):
    """The card ids the client never sees, and the tokens it does."""
    return app.extensions["repetita"].handles


@pytest.fixture
def library():
    result = load_course(COURSE)
    assert result.ok, [str(p) for p in result.fatal]
    return result


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'type="module"' in response.data


def test_state_reports_the_debt_and_the_course(client):
    body = client.get("/api/state").get_json()
    assert body["course"]["id"] == "pt-br-from-pl"
    assert body["quarantined"] == 0
    assert body["cards"] > 0
    # Nothing has been answered, so nothing is owed -- new cards are introduced,
    # not owed, and conflating the two is what made the tile disagree with the
    # per-card counter in the predecessor.
    assert body["owed"] == 0
    assert body["answered_today"] == 0
    assert body["gate_open"] is True


def test_session_serves_every_card_with_a_renderable_form(client, library, handles):
    body = client.get("/api/session").get_json()
    served = {handles.card(c["id"]) for c in body["cards"]}

    # One card per note, not every card: a note's siblings are held back to
    # another day, because the second is otherwise answered from the first
    # rather than from memory (ADR-0001).
    assert served <= {c.id for c in library.cards}
    note_of = {c.id: c.note_id for c in library.cards}
    assert len({note_of[cid] for cid in served}) == len(served)
    assert served, "the session is empty"

    for card in body["cards"]:
        assert card["form"] in ("typein", "wordbank", "flashcard")
        assert card["ask"], "a question with no visible field cannot be answered"
        assert card["fields"]


def test_a_held_back_sibling_is_reported_not_silent(client, library):
    """A queue shorter than the debt needs a visible reason."""
    body = client.get("/api/session").get_json()
    notes = {c.note_id for c in library.cards}
    expected = len(library.cards) - len(notes)
    assert body["buried"] == expected
    assert expected > 0, "the sample course no longer exercises this path"


def test_wordbank_ships_tokens_and_not_the_sentence(client, library, handles):
    """
    Two things changed here when the presenter started choosing the form.

    The expected field is resolved per card rather than assumed to be `answers`:
    a word bank is no longer only ever a `sentence` note, since a multi-word
    `vocab` answer gets one on first contact too.

    And the substring assertion is made against the card's own payload rather
    than the whole session body. A `vocab` note's `produce` answer is its
    `recognize` sibling's question, so once vocab cards reach this loop a
    session-wide substring check cannot tell a leak from two siblings correctly
    served -- which is exactly why `test_open_question_never_carries_its_answer`
    suspends the siblings before making that assertion. That test owns the
    session-wide rule; this one owns "a word bank ships pieces, never the whole".
    """
    cards = {c["id"]: c for c in client.get("/api/session").get_json()["cards"]}
    wordbank = [c for c in cards.values() if c["form"] == "wordbank"]
    assert wordbank, "the course no longer exercises the word-bank path"

    for card in wordbank:
        card_id = handles.card(card["id"])
        served = next(c for c in library.cards if c.id == card_id)
        note = next(n for n in library.notes if n.id == served.note_id)
        expect = library.notetypes[card["notetype"]].cards[card["template"]].expect
        answers = note.answers(expect)
        assert answers, f"{card_id} has no answer to build a word bank from"
        assert sorted(card["tokens"]) == sorted(answers[0].split())
        assert card["tokens"] != answers[0].split()
        raw = json.dumps(card, ensure_ascii=False).encode()
        for answer in answers:
            assert answer.encode() not in raw


def test_first_contact_is_taught_and_the_next_one_examines(client, tmp_path, handles):
    """
    The presenter, reached through the request path rather than in isolation.

    `bom dia` is a two-word `vocab` answer whose card declares choice, typein and
    wordbank. This build cannot render a multiple choice, so first contact
    degrades to the word bank -- assembling it, not producing it from nothing --
    and the second encounter asks for it as declared.
    """
    card_id = "bom-dia#produce"
    served = {handles.card(c["id"]): c for c in client.get("/api/session").get_json()["cards"]}
    assert served[card_id]["form"] == "wordbank"

    token = handles.handle(card_id)
    for _ in range(2):
        answered = client.post("/api/answer", json={"card_id": token, "text": "bom dia"})
        assert answered.get_json()["passed"] is True

    # The log is the second contact's witness: the answered card is scheduled a
    # day out, so it is not in today's queue to be looked at again. Each row
    # carries the form the learner actually saw -- recomputed from the state that
    # preceded the answer, never taken from the client.
    con = connect(tmp_path / "study.db")
    try:
        rows = con.execute(
            "SELECT form FROM review_log WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
    finally:
        con.close()
    assert [r["form"] for r in rows] == ["wordbank", "typein"]


def test_a_correct_answer_is_graded_scheduled_and_revealed(client, library, handles):
    card = next(c for c in library.cards if c.template == "recognize")
    note = next(n for n in library.notes if n.id == card.note_id)
    expected = note.answers("l1")[0]

    body = client.post(
        "/api/answer", json={"card_id": handles.handle(card.id), "text": expected}
    ).get_json()

    assert body["passed"] is True
    assert body["rating"] == 3  # GOOD
    assert body["matched"] == expected
    assert body["due"] is not None
    assert body["answered_today"] == 1
    # The question is closed now, so the answer may be shown -- and must be, or
    # a wrong answer teaches nothing.
    assert expected in body["answers"]
    # The reveal is the complement of what was shown: `l2` was the question and
    # is not repeated, while `explain` was withheld until now.
    assert body["reveal"]["l1"] == expected
    assert body["reveal"]["explain"] == note.text("explain")
    assert "l2" not in body["reveal"]


def test_a_wrong_answer_fails_and_is_kept(client, library, tmp_path, handles):
    card = next(c for c in library.cards if c.template == "recognize")
    body = client.post(
        "/api/answer", json={"card_id": handles.handle(card.id), "text": "zzq-nonsense"}
    ).get_json()
    assert body["passed"] is False
    assert body["rating"] == 1  # AGAIN

    con = connect(tmp_path / "study.db")
    try:
        rows = con.execute("SELECT card_id, answer, form, mode FROM review_log").fetchall()
    finally:
        con.close()
    # Wrong answers are kept deliberately: they are the best distractors anyone
    # will ever have, because they are the mistakes real learners made.
    assert [(r["card_id"], r["answer"], r["mode"]) for r in rows] == [
        (card.id, "zzq-nonsense", "session")
    ]
    assert rows[0]["form"] in ("typein", "wordbank", "flashcard")


def test_an_accent_slip_is_hard_rather_than_a_miss(client, library, handles):
    """
    Course configuration reaching the grader, not engine behaviour: this course
    sets `fold_accents`, so a missing diacritic is HARD. ADR-0002 is why that
    matters -- HARD passing is what stops one dropped accent erasing months of
    schedule.
    """
    card, answer, stripped = None, "", ""
    for candidate in library.cards:
        expect = library.notetypes[candidate.notetype].cards[candidate.template].expect
        note = next(n for n in library.notes if n.id == candidate.note_id)
        for accepted in note.answers(expect):
            if strip_accents(accepted) != accepted:
                card, answer, stripped = candidate, accepted, strip_accents(accepted)
                break
        if card:
            break
    assert card, "no answer in this course carries a diacritic to drop"

    body = client.post(
        "/api/answer", json={"card_id": handles.handle(card.id), "text": stripped}
    ).get_json()
    assert body["rating"] == 2  # HARD
    assert body["passed"] is True
    assert body["matched"] == answer


def test_the_whole_session_can_be_answered(client, library, handles):
    answers = {}
    for card in library.cards:
        notetype = library.notetypes[card.notetype]
        note = next(n for n in library.notes if n.id == card.note_id)
        answers[card.id] = note.answers(notetype.cards[card.template].expect)[0]

    # Two passes, because a brand-new card gets a learning step: answered
    # correctly it is due again in the same session (sm2.LEARNING_STEPS), which
    # is the behaviour the queue exists to serve. Only the second pass puts it on
    # the day scale.
    seen = 0
    for _ in range(2):
        session = client.get("/api/session").get_json()
        assert session["cards"], "the learning step did not bring the cards back"
        for card in session["cards"]:
            body = client.post(
                "/api/answer",
                json={"card_id": card["id"], "text": answers[handles.card(card["id"])]},
            ).get_json()
            assert body["passed"] is True, card["id"]
            seen += 1

    state = client.get("/api/state").get_json()
    assert state["answered_today"] == seen
    assert state["owed"] == 0

    # Nothing owed and nothing new left, so what remains is consolidation --
    # extra practice, flagged as such so the learner can see where the plan ends.
    after = client.get("/api/session").get_json()
    assert after["consolidating"] is True


def test_a_card_id_never_carries_its_own_answer(client, library):
    """
    The boundary of the guarantee above, stated rather than left to be
    rediscovered. Every *field* is filtered; the id is not a field.
    """
    body = client.get("/api/session")
    served = {c["id"] for c in body.get_json()["cards"]}
    for card in library.cards:
        if card.id not in served:
            continue
        expect = library.notetypes[card.notetype].cards[card.template].expect
        note = next(n for n in library.notes if n.id == card.note_id)
        for answer in note.answers(expect):
            assert answer.encode() not in body.data, f"{card.id} carries its own answer"


# --- refusals -------------------------------------------------------------


def test_an_unknown_card_is_refused(client):
    response = client.post("/api/answer", json={"card_id": "not-a-real-handle", "text": "x"})
    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown_card"}


@pytest.mark.parametrize("day", ["yesterday", "2026-13-01", "06-09-2026"])
def test_a_malformed_day_is_refused_not_ignored(client, day):
    """
    Substituting today would file a whole session's rows under the wrong date,
    silently. Content loading refuses a bad `lesson:` for the same reason.
    """
    for path in (f"/api/state?day={day}", f"/api/session?day={day}"):
        response = client.get(path)
        assert response.status_code == 400
        assert response.get_json() == {"error": "bad_day"}

    response = client.post("/api/answer", json={"card_id": "x", "day": day})
    assert response.status_code == 400


def test_the_learners_day_is_used_for_the_log(client, library, tmp_path, handles):
    """
    The day is the learner's, not the server's -- passed separately from the
    instant so an evening session in one timezone is not filed under another's
    tomorrow.
    """
    card = next(iter(library.cards))
    client.post(
        "/api/answer", json={"card_id": handles.handle(card.id), "text": "x", "day": "2026-01-02"}
    )

    con = connect(tmp_path / "study.db")
    try:
        rows = con.execute("SELECT day, review_datetime FROM review_log").fetchall()
    finally:
        con.close()
    assert rows[0]["day"] == "2026-01-02"
    assert not rows[0]["review_datetime"].startswith("2026-01-02")

    assert client.get("/api/state?day=2026-01-02").get_json()["answered_today"] == 1
    assert client.get("/api/state?day=2026-01-03").get_json()["answered_today"] == 0


def test_content_is_rebuilt_but_progress_is_not(tmp_path, library):
    """
    Restarting the app re-derives the content cache and leaves study history
    alone. This is the separation the whole store design rests on.
    """
    db = tmp_path / "study.db"
    card = next(iter(library.cards))

    # Handles are per-run, so each app is asked for its own. Borrowing the
    # fixture app's token is the "unknown handle after a restart" case and would
    # make this test pass for the wrong reason.
    first_app = create_app(COURSE, db_path=db)
    token = first_app.extensions["repetita"].handles.handle(card.id)
    first_app.test_client().post(
        "/api/answer", json={"card_id": token, "text": "x", "day": "2026-01-02"}
    )

    second = create_app(COURSE, db_path=db).test_client()
    assert second.get("/api/state?day=2026-01-02").get_json()["answered_today"] == 1


def test_serve_refuses_an_ambiguous_course_directory(tmp_path, capsys):
    from repetita.cli import main

    for name in ("a", "b"):
        _write_course(tmp_path / name, "vocab", _sentinels("vocab"))
    assert main(["serve", str(tmp_path)]) == 1
    assert "several courses" in capsys.readouterr().out


def test_a_note_that_leaks_is_quarantined_rather_than_served(tmp_path):
    """A leaking exercise is answered correctly every time, which opens the gate."""
    fields = dict(_sentinels("vocab"))
    fields["l1"] = fields["l2"]  # the prompt is now the answer
    course = _write_course(tmp_path / "course", "vocab", fields)

    client = _app(course, tmp_path).test_client()
    state = client.get("/api/state").get_json()
    assert state["quarantined"] == 1
    assert client.get("/api/session").get_json()["cards"] == []


def test_every_builtin_card_has_a_renderable_form():
    """A card no form module can draw is a card that cannot be studied."""
    from repetita.web.serialize import SUPPORTED_FORMS

    for name, notetype in BUILTIN.items():
        for template, card in notetype.cards.items():
            assert set(card.forms) & set(SUPPORTED_FORMS), f"{name}.{template} is unrenderable"
