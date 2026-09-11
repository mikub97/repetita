"""
How well a set of material is known.

One definition, because three screens ask the same question and answering it
three ways would mean three numbers that disagree about the same cards.

The obvious version does not work, and it was measured before being discarded.
On the real database -- 757 live cards -- the buckets are 673 new, 31 retired,
25 young, 24 learning, 4 suspended, and **0 mature**. Nothing had yet reached the
21-day threshold, so "how much of this set is mature" paints every set the same
grey on the first day and keeps doing so for three weeks.

The second trap is "I know this". All 31 retired cards are `declared` -- a claim
the learner made, not evidence the scheduler gathered. Leaving them out means a
set marked entirely known reads as nothing known; folding them into `mature`
silently promotes the claim to evidence, which is the one distinction
`declare_known` exists to keep (see its docstring, and ADR-0004 on why an
unverifiable correction is a claim rather than a fix).

So: four states, and the claim stays visible as a claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from .buckets import LEARNING, MATURE, NEW, RETIRED, SUSPENDED, YOUNG

#: Known because the schedule says so.
EARNED = "earned"
#: Known because the learner said so. Never merged with the above.
DECLARED = "declared"

UNTOUCHED = "untouched"
STARTED = "started"
GETTING_THERE = "getting-there"
DONE = "done"

#: Worst to best, for anything that needs to sort or pick a representative.
ORDER = (UNTOUCHED, STARTED, GETTING_THERE, DONE)


@dataclass(frozen=True, slots=True)
class Mastery:
    """The composition of a set, and the one-glance summary of it."""

    total: int = 0
    untouched: int = 0
    #: Being learned or recently learned -- `learning` and `young` together.
    working: int = 0
    #: Retired by evidence, or mature.
    earned: int = 0
    #: Retired because the learner said "I know this".
    declared: int = 0
    #: Out of circulation for a reason that is not progress.
    suspended: int = 0

    @property
    def handled(self) -> int:
        """Cards that are no longer simply waiting."""
        return self.working + self.earned + self.declared

    @property
    def progress(self) -> float:
        """
        0.0 to 1.0 -- how much of the set has been dealt with at all.

        Deliberately not "how much is mature". Mastery that only moves after
        three weeks is not a signal anybody can act on while deciding what to
        study tonight.
        """
        countable = self.total - self.suspended
        return self.handled / countable if countable > 0 else 0.0

    @property
    def state(self) -> str:
        """The dot. The bar carries the detail; this is the glance."""
        if not self.handled:
            return UNTOUCHED
        share = self.progress
        if share >= 0.999:
            return DONE
        return GETTING_THERE if share >= 0.5 else STARTED


def tally(buckets: dict[str, int], *, declared: int = 0) -> Mastery:
    """
    Fold a bucket count into the four states.

    `declared` is passed separately because `bucket_of` collapses every
    retirement into `retired`, and the difference between a claim and evidence
    is not recoverable from the bucket alone -- it lives in
    `card_state.retired_reason`.
    """
    retired = buckets.get(RETIRED, 0)
    earned = max(0, retired - declared)
    return Mastery(
        total=sum(buckets.values()),
        untouched=buckets.get(NEW, 0),
        working=buckets.get(LEARNING, 0) + buckets.get(YOUNG, 0),
        earned=earned + buckets.get(MATURE, 0),
        declared=min(declared, retired),
        suspended=buckets.get(SUSPENDED, 0),
    )
