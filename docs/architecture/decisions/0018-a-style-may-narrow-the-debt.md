# ADR-0018: A study style may narrow the debt, bounded and visible

**Status:** accepted, 2026-09-12
**Supersedes:** [ADR-0007](0007-a-plan-is-data.md), *"What was rejected →
Letting a plan filter the queue"*
**Context:** [ADR-0017](0017-how-i-study-is-data-and-not-a-plan.md),
CLAUDE.md rule 1

## Context

ADR-0007 rejected letting a plan filter the queue, and said why:

> The debt is not the plan's to touch, and a learner who could hide 200 owed
> cards behind a priority list would, once, and then find them again a month
> later at four times the size.

That reasoning is still correct. It is being overridden anyway, deliberately,
and this ADR exists to say what changed and what it cost.

What changed is the shape of the thing asking. ADR-0007 was reasoning about a
**plan**: an object you build in a designer, activate, and then forget is
active. The failure it describes is not really "the debt got hidden" — it is
"the debt got hidden by something the learner stopped thinking about". A plan
has no expiry, no presence on the screen where you study, and no reason to come
back to your attention.

What is being added is a **focus** on a study style (ADR-0017), which is a
different object in exactly the three ways that matter.

## Decision

**A style may narrow which owed cards are served.** `study_styles.focus` is a
selector; empty is the whole debt, which is what everybody has until they say
otherwise.

Three guardrails, and they are the feature rather than a hedge around it.

**1. It expires, and by default it expires in a week.**

This is the one that does the work, and it is the one to keep if any are ever
cut. Visibility and boundedness are not the same thing: a counter telling you
190 cards are hidden is information, and people are extremely good at reading a
number every day and doing nothing about it. A date is what makes the decision
end on its own. "Do odwołania" exists, because refusing it outright would just
push people to un-set and re-set the focus forever, but it is behind a confirm
that says what it means.

`active_focus()` is a **read**. A lapse is not written back. A read that wrote
would file the lapse as an edit at whatever moment a page happened to load, put
a revision in the log the learner did not make, and destroy the row they would
want to renew.

**2. The true debt is never narrowed anywhere it is counted.**

`owed_count` takes no focus and **has no parameter through which one could reach
it** — not a keyword, not a default argument. The gap is counted by a separate
function, `owed_hidden`, which has to be called by name. A signature somebody
cannot reach through outlives a comment asking them not to; this repository has
the `plan_knobs` dials to prove what happens to a rule with no mechanism.

`forecast` reports the true debt too. It takes an optional `focus_ids` used by
one caller, the preview, to draw a **second** curve beside the first.

**3. It is visible from where you study, including when it is not biting.**

The Study screen says `N zaległych ukrytych przez skupienie` with a "Pokaż
wszystko" button beside it, one click from the session it is affecting. When the
focus happens to hide nothing today it says *"skupienie włączone, nic dziś nie
ukrywa"* rather than going quiet — a guardrail that hides itself whenever it is
not biting is one you learn to forget.

The header chip carries the mode name on every screen, so a non-default style is
never only visible on the page that sets it.

## Why the preview is part of this and not decoration

The screen draws both forecast curves: the debt as it really is, and what the
focus would serve. The gap between them is the cost, drawn while you are
choosing, not discovered afterwards. That is only affordable because the
policies are pure — ADR-0007's own argument, applied on the screen where the
stakes are higher.

## Consequences

Somebody can still do the thing ADR-0007 warned about. They now have to do it on
purpose, past a confirm, with the number on screen and a date that undoes it.
That is the whole difference, and it is a real one: the failure ADR-0007
describes is a failure of *attention*, not of arithmetic.

If this turns out badly, the evidence will exist. Every style change writes a
revision and every answer records which one was in force (ADR-0017), so "how did
the six weeks with a focus actually go" is answerable rather than a matter of
recollection. That is the reason the write side of revisions shipped before
anything read it.

**What is not permitted, and did not change:** a focus narrows what is
*served*. It does not mark anything answered, does not touch `card_state`, does
not alter a due date, and no import or reload interacts with it. Hidden is not
paid, and the screen says so in those words.
