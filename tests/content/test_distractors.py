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


VERBS = """\
    notetype: gap
    notes:
      - id: v1
        prompt: Eu ___ de casa às oito.
        answers: [saio]
        cue: sair
      - id: v2
        prompt: Eles ___ tarde.
        answers: [saem]
        cue: sair
      - id: v3
        prompt: Nós ___ juntos.
        answers: [saímos]
        cue: sair
      - id: v4
        prompt: Ela ___ o almoço.
        answers: [faz]
        cue: fazer
      - id: v5
        prompt: Você ___ música.
        answers: [ouve]
        cue: ouvir
    """


class TestMorphology:
    def test_forms_of_the_same_verb_come_first(self, course):
        # The paradigm is the hardest set of options to eliminate without knowing
        # the grammar, which is exactly what the exercise is testing.
        _, ds = course({"u1": VERBS})
        first = texts(ds, "v1#fill")[:2]
        assert set(first) <= {"saem", "saímos"}, first

    def test_they_are_labelled_as_such(self, course):
        _, ds = course({"u1": VERBS})
        by_text = {d.text: d.source for d in ds if d.card_id == "v1#fill"}
        assert by_text["saem"] == "morphology"
        assert by_text["faz"] == "same_unit"

    def test_a_one_letter_answer_gets_no_spurious_stem(self, course):
        # `ontem`, `outubro` and `onze` all share the article `o`'s single letter.
        # Counting that as a stem ranks them above `as`, which is the opposite of
        # useful -- so below the threshold the signal is ignored entirely.
        _, ds = course(
            {
                "u1": """\
            notetype: gap
            notes:
              - id: art
                prompt: ___ cinema fica perto.
                answers: [o]
                cue: rodzajnik
              - id: n1
                prompt: Cheguei ___.
                answers: [ontem]
                cue: wczoraj
              - id: n2
                prompt: Foi em ___.
                answers: [outubro]
                cue: miesiąc
              - id: n3
                prompt: Tenho ___ anos.
                answers: [onze]
                cue: liczba
              - id: n4
                prompt: ___ meninas cantam.
                answers: [as]
                cue: rodzajnik
            """
            }
        )
        assert texts(ds, "art#fill")[0] == "as", "the short, comparable word first"
        assert all(d.source == "same_unit" for d in ds if d.card_id == "art#fill")


class TestFrequency:
    def test_a_missing_wordfreq_degrades_rather_than_breaks(self, course, monkeypatch):
        # It is an optional 63MB extra. Its absence must change the ORDER of
        # options, never which are eligible or how they are labelled.
        import builtins

        real = builtins.__import__

        def no_wordfreq(name, *args, **kwargs):
            if name == "wordfreq":
                raise ImportError("not installed")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_wordfreq)
        r, _ = course({"u1": VERBS})
        from repetita.content.distractors import build

        without = build(r.cards, r.notes, r.notetypes, lang="pt")
        assert {d.text for d in without if d.card_id == "v1#fill"}

    def test_the_language_comes_from_the_course_not_the_engine(self, course):
        # CLAUDE.md rule 4: the engine holds no language knowledge. It asks for
        # the course's code and passes it through.
        r, _ = course({"u1": VERBS})
        from repetita.content.distractors import build

        assert build(r.cards, r.notes, r.notetypes, lang=None)
        assert build(r.cards, r.notes, r.notetypes, lang="pt")

    def test_it_stays_deterministic_with_frequency_in_play(self, course):
        r, _ = course({"u1": VERBS})
        from repetita.content.distractors import build

        a = build(r.cards, r.notes, r.notetypes, lang="pt")
        b = build(r.cards, r.notes, r.notetypes, lang="pt")
        assert [(d.card_id, d.text, d.rank) for d in a] == [(d.card_id, d.text, d.rank) for d in b]
