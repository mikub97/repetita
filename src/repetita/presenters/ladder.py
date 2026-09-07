"""
The recognition-to-production ladder: first contact teaches, later ones examine.

A card that has never been answered is asked in a recognition form -- a multiple
choice for a word, a word bank for a sentence -- and in its declared, productive
form from the second encounter on.

Why it exists, rather than always asking a card the way its author declared it:
in the app this engine was extracted from, a vocabulary pack scored 10% correct
over its first ten answers. Producing an unseen word from an L1 cue is close to
guessing, and the miss is not free -- it costs ease and it counts towards the
gate that decides whether new material may be introduced at all. So a first
contact spent examining does not merely fail to teach; it slows down everything
after it.
"""

from __future__ import annotations

from ..core.protocols import PresentationContext

#: A one-word answer is a word; two or more make a sentence. The same threshold
#: as the word bank's own minimum, and for the same reason: a word bank holding
#: one token is the answer with extra steps.
SENTENCE_MIN_TOKENS = 2

#: How many encounters are taught rather than examined. 0 switches the ladder
#: off, which is the honest way to measure whether it is worth anything.
LADDER_STEPS = 1

#: Forms ordered by how much production they demand of the learner: recognising
#: an answer among options is easier than assembling it from its own words, which
#: is easier than producing it from nothing.
#:
#: The ladder only ever moves *down* this list. Without the ordering, a card
#: whose author already declared an easy form -- `phrase.say` is a flashcard --
#: would have first contact made *harder* by the very policy meant to soften it.
DEMAND: tuple[str, ...] = ("flashcard", "choice", "wordbank", "typein")


def _demand(form: str) -> int | None:
    """How productive a form is, or None for a form this ladder does not rank."""
    try:
        return DEMAND.index(form)
    except ValueError:
        return None


class LadderPresenter:
    """
    Recognition on first contact, the declared form after that.

    Keyed on `seen`, never on `reps`. `reps` is reset to zero by every lapse, so
    a card that is genuinely hard would keep being demoted to multiple choice and
    could never be tested properly -- it would look like it was being learned
    while never once being asked to produce anything. `seen == 0` is the real
    "have we met before" question, and it is the one asked here.
    """

    name = "ladder"

    def __init__(self, steps: int = LADDER_STEPS) -> None:
        #: Encounters shown in a recognition form. <= 0 disables the ladder.
        self.steps = steps

    def choose(self, declared_form: str, ctx: PresentationContext) -> str:
        if self.steps <= 0 or ctx.seen >= self.steps:
            return declared_form

        # Sentence-shaped cards assemble rather than choose: four whole sentences
        # to pick between is a wall of text, and building the sentence out of its
        # own words is the thing being learned anyway.
        easier = "wordbank" if ctx.answer_tokens >= SENTENCE_MIN_TOKENS else "choice"

        # Degrade rather than fail. A form the build cannot render, or that this
        # card never declared, is simply not on offer -- `choice` is exactly that
        # today, because a multiple choice cannot be served without putting the
        # answer beside its distractors while the question is open. The card is
        # then asked as declared, which is worse teaching but not a broken page.
        if easier not in ctx.available_forms:
            return declared_form

        # An unrankable declared form is left alone: a plugin's own form may
        # already be gentler than anything here, and guessing would be how the
        # ladder makes a card harder behind an author's back.
        declared_demand = _demand(declared_form)
        if declared_demand is None or DEMAND.index(easier) >= declared_demand:
            return declared_form
        return easier
