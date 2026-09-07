"""
The answer-leak quarantine.

A leaking exercise is worse than a missing one: it is answered correctly every
time, so it inflates accuracy, which opens the new-material gate. And it looks
exactly like a card you know well. These tests pin that it is a quarantine and
not a warning.
"""

import textwrap

import pytest

from repetita.content.loader import load_course

COURSE = """\
format_version: 1
id: t
l2: {code: pt, variant: pt-BR}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


@pytest.fixture
def course(tmp_path):
    def build(notes_yaml: str):
        (tmp_path / "course.yaml").write_text(COURSE)
        d = tmp_path / "units" / "01" / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (d / "n.yaml").write_text(textwrap.dedent(notes_yaml))
        return load_course(tmp_path)

    return build


class TestQuarantine:
    def test_the_original_bug_is_caught(self, course):
        # The exercise this whole package was built around: the cue restated the
        # answer instead of posing the task.
        r = course("""\
            notes:
              - id: fds
                prompt: Vou viajar no ___ de semana.
                answers: [fim]
                cue: fim de semana = weekend
            """)
        leak = next(p for p in r.problems if p.kind == "leak")
        assert leak.fatal
        assert leak.note_id == "fds"
        assert "cue" in leak.detail

    def test_a_leaking_note_never_reaches_the_pool(self, course):
        r = course("""\
            notes:
              - id: leaky
                prompt: Vou viajar no ___ de semana.
                answers: [fim]
                cue: fim de semana
              - id: clean
                prompt: Eu ___ de casa às oito.
                answers: [saio]
                cue: sair, presente, 1. os. lp.
            """)
        assert [n.id for n in r.notes] == ["clean"]
        assert [c.note_id for c in r.cards] == ["clean"]
        assert not r.ok, "validate must exit non-zero"

    def test_a_prompt_identical_to_the_answer_has_nothing_to_do(self, course):
        r = course("""\
            notes:
              - id: nothing
                notetype: transform
                prompt: Eu compro verduras na feira.
                instruction: no pretérito perfeito
                answers: ["Eu compro verduras na feira."]
            """)
        leak = next(p for p in r.problems if p.kind == "leak")
        assert leak.fatal
        assert "identical" in leak.detail
        assert r.notes == []

    def test_the_word_boundary_is_respected(self, course):
        # `a` must not match inside `casa`, or half the corpus would quarantine.
        r = course("""\
            notes:
              - id: ok
                prompt: Ele mora em ___ casa velha.
                answers: [uma]
                cue: rodzajnik nieokreślony, r.ż.
            """)
        assert r.ok
        assert [n.id for n in r.notes] == ["ok"]


class TestAccentsAreNotLeaks:
    def test_accent_only_match_is_a_warning_and_still_serves(self, course):
        # `esta` (this) and `está` (is) are different words. Folding accents
        # before looking for a leak would bury real leaks under false positives
        # exactly like this one.
        r = course("""\
            notes:
              - id: ser-estar
                prompt: Ele ___ doente esta semana.
                answers: [está]
                cue: ser czy estar, stan przejściowy
            """)
        warning = next(p for p in r.problems if p.kind == "leak")
        assert not warning.fatal
        assert "accents" in warning.detail
        assert [n.id for n in r.notes] == ["ser-estar"], "it must still serve"
        assert r.ok, "a warning alone does not fail validation"

    def test_but_strict_mode_fails_on_it(self, course):
        r = course("""\
            notes:
              - id: ser-estar
                prompt: Ele ___ doente esta semana.
                answers: [está]
                cue: stan przejściowy
            """)
        assert r.warnings and not r.fatal


class TestPerCard:
    def test_a_field_is_judged_against_its_own_card_s_answer(self, course):
        # In a `vocab` note `l1` is the question for `produce` and the answer for
        # `recognize`. Checking per note rather than per card cannot express
        # that, which is why the check iterates card templates.
        r = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
                example_l1: Mieszkam tam od lat.
            """)
        assert r.ok, "a plain vocab note must not trip the check"
        assert len(r.cards) == 2

    def test_an_l1_example_that_gives_away_the_gloss_is_caught(self, course):
        r = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
                example_l1: To jest dom.
            """)
        leak = next(p for p in r.problems if p.kind == "leak" and p.fatal)
        assert "recognize" in leak.detail, "the message must name the card that leaks"

    def test_options_may_contain_the_answer_because_that_is_their_job(self, course):
        r = course("""\
            notes:
              - id: genero
                prompt: ___ cinema fica perto da minha casa.
                answers: [o]
                options: [o, a, os, as]
                cue: rodzajnik określony
            """)
        assert r.ok
        assert [n.id for n in r.notes] == ["genero"]


class TestShape:
    def test_a_choice_needs_three_options(self, course):
        r = course("""\
            notes:
              - id: c
                prompt: ___ cinema fica perto.
                answers: [o]
                options: [o, a]
                cue: rodzajnik
            """)
        assert any("at least 3 options" in p.detail for p in r.fatal)

    def test_exactly_one_option_must_be_correct(self, course):
        r = course("""\
            notes:
              - id: c
                prompt: ___ cinema fica perto.
                answers: [o]
                options: [x, y, z]
                cue: rodzajnik
            """)
        assert any("exactly one option" in p.detail for p in r.fatal)

    def test_a_gap_without_a_cue_is_only_a_warning(self, course):
        # Sometimes the sentence itself forces the answer, so this is worth a
        # look rather than a refusal.
        r = course("""\
            notes:
              - id: c
                prompt: Compro ___ na feira.
                answers: [verduras]
            """)
        assert any(p.kind == "ambiguous" and not p.fatal for p in r.problems)
        assert r.ok
