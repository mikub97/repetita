"""
The recognition-to-production ladder.

The test this file exists for is `test_a_lapsed_card_is_still_a_card_we_have_met`.
Everything else here restates the issue; that one restates the bug the issue was
written to prevent, where a card keyed on `reps` -- which every lapse resets to
zero -- would be demoted to multiple choice forever and never once be asked to
produce anything.
"""

from __future__ import annotations

import pytest

from repetita import presenters
from repetita.core.protocols import PresentationContext, Presenter
from repetita.presenters.ladder import LadderPresenter

WORD_FORMS = ("choice", "typein", "flashcard")
SENTENCE_FORMS = ("choice", "wordbank", "typein")


def ctx(**kwargs) -> PresentationContext:
    base = {"seen": 0, "lapses": 0, "available_forms": WORD_FORMS, "answer_tokens": 1}
    return PresentationContext(**{**base, **kwargs})


def word(**kwargs) -> PresentationContext:
    return ctx(**{"available_forms": WORD_FORMS, "answer_tokens": 1, **kwargs})


def sentence(**kwargs) -> PresentationContext:
    return ctx(**{"available_forms": SENTENCE_FORMS, "answer_tokens": 6, **kwargs})


ladder = LadderPresenter()


class TestFirstContact:
    def test_a_word_never_answered_is_recognised_not_produced(self):
        assert ladder.choose("typein", word()) == "choice"

    def test_a_sentence_never_answered_is_assembled_not_produced(self):
        """Four whole sentences to choose between is a wall of text; the word
        bank is the recognition rung for anything sentence-shaped."""
        assert ladder.choose("typein", sentence()) == "wordbank"

    def test_the_shape_is_the_answer_length_not_the_note_type(self):
        """A two-word answer is sentence-shaped enough to assemble."""
        assert ladder.choose("typein", ctx(available_forms=SENTENCE_FORMS, answer_tokens=2)) == (
            "wordbank"
        )
        assert ladder.choose("typein", ctx(available_forms=SENTENCE_FORMS, answer_tokens=1)) == (
            "choice"
        )

    def test_an_unknown_answer_length_is_treated_as_a_word(self):
        assert ladder.choose("typein", ctx(available_forms=SENTENCE_FORMS, answer_tokens=0)) == (
            "choice"
        )


class TestSecondContactOnwards:
    def test_a_card_answered_once_is_asked_as_declared(self):
        assert ladder.choose("typein", word(seen=1)) == "typein"
        assert ladder.choose("typein", sentence(seen=1)) == "typein"

    def test_a_lapsed_card_is_still_a_card_we_have_met(self):
        """
        The whole point of keying on `seen` rather than `reps`.

        `reps` is reset to zero by every lapse, so a card failed many times would
        be handed back to first-contact treatment forever: it would look like it
        was being learned while never once being asked to produce anything. A
        card seen fifty times is not a first contact, however badly it is going.
        """
        struggling = word(seen=50, lapses=12)
        assert ladder.choose("typein", struggling) == "typein"
        assert ladder.choose("typein", sentence(seen=3, lapses=3)) == "typein"


class TestDegrading:
    def test_an_unavailable_easier_form_falls_back_to_the_declared_one(self):
        """
        The live case, not a hypothetical: this build does not serve `choice` at
        all, because a multiple choice puts the answer on the screen beside its
        distractors while the question is open. The ladder must ask the card as
        declared, not raise and not return a form nothing can render.
        """
        assert ladder.choose("typein", word(available_forms=("typein", "flashcard"))) == "typein"
        assert ladder.choose("typein", sentence(available_forms=("typein",))) == "typein"

    def test_no_available_forms_at_all_still_yields_something_askable(self):
        assert ladder.choose("typein", word(available_forms=())) == "typein"

    def test_the_ladder_only_returns_a_form_that_was_on_offer(self):
        for context in (word(), sentence(), word(seen=4), sentence(available_forms=())):
            chosen = ladder.choose("typein", context)
            assert chosen == "typein" or chosen in context.available_forms

    def test_it_never_makes_a_first_contact_harder(self):
        """
        A `phrase` card is declared as a flashcard, which is already gentler than
        assembling the sentence from its words. Softening it into a word bank
        would be the ladder doing the opposite of its job.
        """
        assert ladder.choose("flashcard", sentence(available_forms=("flashcard", "wordbank"))) == (
            "flashcard"
        )
        assert ladder.choose("choice", word()) == "choice"

    def test_a_form_it_cannot_rank_is_left_alone(self):
        assert ladder.choose("speaking", word()) == "speaking"


class TestOffSwitch:
    def test_zero_steps_always_returns_the_declared_form(self):
        off = LadderPresenter(steps=0)
        for context in (word(), sentence(), word(seen=0, lapses=0)):
            assert off.choose("typein", context) == "typein"

    def test_more_steps_teach_for_longer(self):
        patient = LadderPresenter(steps=3)
        assert patient.choose("typein", word(seen=2)) == "choice"
        assert patient.choose("typein", word(seen=3)) == "typein"


class TestRegistry:
    def test_the_ladder_is_registered_and_satisfies_the_protocol(self):
        assert "ladder" in presenters.names()
        assert isinstance(presenters.get("ladder"), Presenter)

    def test_the_default_is_resolvable_without_naming_it(self):
        assert presenters.get() is presenters.get(presenters.DEFAULT)

    def test_an_unknown_presenter_is_refused_with_the_alternatives(self):
        with pytest.raises(LookupError) as excinfo:
            presenters.get("no-such-presenter")
        assert "ladder" in str(excinfo.value)
