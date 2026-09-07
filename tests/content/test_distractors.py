"""
Distractor generation.

Weak options are answered by elimination, so the card is answered correctly
whatever the learner knows. That inflates accuracy, and accuracy opens the
new-material gate -- which makes this a scheduling concern wearing a UI costume.
"""

import textwrap

import pytest

from repetita.content.distractors import KEEP, MIN_OPTIONS, build
from repetita.content.loader import load_course

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


@pytest.fixture
def course(tmp_path):
    def make(files: dict[str, str]):
        root = tmp_path / "c"
        (root / "course.yaml").parent.mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(COURSE)
        for unit, body in files.items():
            d = root / "units" / unit / "notes"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{unit}.yaml").write_text(textwrap.dedent(body))
        r = load_course(root)
        assert not r.fatal, [str(p) for p in r.problems if p.fatal]
        return r, build(r.cards, r.notes, r.notetypes)

    return make


ARTICLES = """\
    notetype: gap
    notes:
      - id: a1
        prompt: ___ cinema fica perto.
        answers: [o]
        cue: rodzajnik
      - id: a2
        prompt: ___ casa é bonita.
        answers: [a]
        cue: rodzajnik
      - id: a3
        prompt: ___ livros são novos.
        answers: [os]
        cue: rodzajnik
      - id: a4
        prompt: ___ meninas cantam.
        answers: [as]
        cue: rodzajnik
    """


def texts(ds, card_id):
    return [d.text for d in ds if d.card_id == card_id]


class TestGeneration:
    def test_options_come_from_the_same_kind_of_answer(self, course):
        _, ds = course({"u1": ARTICLES})
        assert sorted(texts(ds, "a1#fill")) == ["a", "as", "os"]

    def test_the_answer_is_never_offered_against_itself(self, course):
        _, ds = course({"u1": ARTICLES})
        for card_id in ("a1#fill", "a2#fill", "a3#fill", "a4#fill"):
            answer = card_id[1]
            assert answer not in texts(ds, card_id)

    def test_a_curated_distractor_wins(self, course):
        _, ds = course(
            {
                "u1": """\
            notetype: gap
            notes:
              - id: a5
                prompt: ___ problema é sério.
                answers: [o]
                distractors: [a, uma]
                cue: rodzajnik
              - id: a6
                prompt: ___ casa é bonita.
                answers: [essa]
                cue: zaimek
            """
            }
        )
        chosen = texts(ds, "a5#fill")
        assert chosen[:2] == ["a", "uma"], "the author's expected mistakes come first"
        assert [d.source for d in ds if d.card_id == "a5#fill"][:2] == ["curated"] * 2

    def test_a_curated_distractor_equal_to_the_answer_is_dropped(self, course):
        # An author can make this mistake; offering the answer twice would make
        # the question unanswerable.
        _, ds = course(
            {
                "u1": """\
            notetype: gap
            notes:
              - id: x
                prompt: ___ cinema fica perto.
                answers: [o]
                distractors: [o, a]
                cue: rodzajnik
              - id: y
                prompt: ___ casa é bonita.
                answers: [a]
                cue: rodzajnik
            """
            }
        )
        assert "o" not in texts(ds, "x#fill")

    def test_the_same_unit_is_preferred_over_the_wider_course(self, course):
        _, ds = course(
            {
                "u1": """\
                notetype: gap
                notes:
                  - id: near
                    prompt: ___ cinema fica perto.
                    answers: [o]
                    cue: rodzajnik
                  - id: sibling
                    prompt: ___ casa é bonita.
                    answers: [a]
                    cue: rodzajnik
                """,
                "u2": """\
                notetype: gap
                notes:
                  - id: far
                    prompt: ___ livros são novos.
                    answers: [os]
                    cue: rodzajnik
                """,
            }
        )
        assert texts(ds, "near#fill")[0] == "a", "the lesson at hand comes first"

    def test_it_is_deterministic(self, course):
        # Anything random here churns the database on every content rebuild and
        # makes a content diff unreadable.
        r, first = course({"u1": ARTICLES})
        second = build(r.cards, r.notes, r.notetypes)
        assert [(d.card_id, d.text, d.rank) for d in first] == [
            (d.card_id, d.text, d.rank) for d in second
        ]

    def test_it_keeps_a_bounded_number(self, course):
        many = "notetype: gap\nnotes:\n" + "".join(
            f"  - id: n{i}\n    prompt: ___ tem {i}.\n    answers: [w{i}]\n    cue: c\n"
            for i in range(30)
        )
        _, ds = course({"u1": many})
        assert len(texts(ds, "n0#fill")) == KEEP

    def test_a_lone_note_gets_none(self, course):
        _, ds = course(
            {
                "u1": """\
            notetype: gap
            notes:
              - id: only
                prompt: ___ cinema fica perto.
                answers: [o]
                cue: rodzajnik
            """
            }
        )
        assert texts(ds, "only#fill") == []
        assert len(texts(ds, "only#fill")) < MIN_OPTIONS, "so choice must not be offered"

    def test_note_types_without_a_choice_form_get_nothing(self, course):
        # A word bank or a spoken phrase has no options to offer.
        _, ds = course(
            {
                "u1": """\
            notetype: phrase
            notes:
              - id: p1
                situation: Witasz grupę.
                target: Bom dia a todos.
              - id: p2
                situation: Żegnasz się.
                target: Até logo.
            """
            }
        )
        assert ds == []
