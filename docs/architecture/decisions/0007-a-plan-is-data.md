# ADR-0007: A study plan is data, and every answer records which revision produced it

**Status:** accepted, 2026-09-10
**Context:** [ADR-0003](0003-scheduler-is-a-plugin.md), the lesson designer

## Context

The engine had exactly one idea of what to study next: `policies/daily`, which
follows the course's own order. That is the right default and it stays the
default. What it cannot express is a learner's own emphasis — *"I need numbers
before the trip"*, *"less grammar for a fortnight"*, *"more listening"*.

Two things were already in the codebase pointing at this and doing nothing.
`Course.tag_weights` is documented as *"relative share of each tag when new
material is introduced"*, is populated in `course.yaml`, and was read by no code.
`Course.path` declares an order over units and was read by no code either. The
intent has been recorded in this project longer than any implementation of it.

## Decision

A **plan** is four tables: `study_plans`, `plan_priorities` (the ordered list),
`plan_knobs`, and `plan_revisions`. It is progress-side data — never rebuilt,
never derived.

**Every change writes a revision, and `review_log.plan_revision_id` records which
revision was in force when an answer was given.**

That last clause is the whole reason this is an ADR rather than a feature.

## Why the revision link matters more than the feature

ADR-0003 exists because the predecessor stored aggregates — `seen`, `correct`,
`wrong` — and threw the sequence away, and that is the one decision in the old
design *that could not be undone after the fact*. No amount of later work
recovers a sequence nobody wrote down.

"Did making it harder actually help?" is the same shape of question. So is
"which priority list did I learn fastest under?" and "was that fortnight of
grammar worth it?". None of them can be answered from a plan's *current* state,
because by the time the question is interesting the plan has been edited a dozen
times. They can only be answered if each answer knows which plan produced it.

Adding the column later would work from that day forward and leave every earlier
answer unattributable — which is precisely the failure ADR-0003 describes. So it
is recorded from the first day, before anything reads it.

## Consequences

* `plan_revisions` is append-only, and deleting a plan **keeps** its revisions.
  Answers point at those rows; removing them would turn a recorded fact into a
  dangling id nobody can resolve.
* `set_priorities` replaces the list wholesale rather than editing rows. The list
  *is* the ordering, and applying two independent edits to it would produce an
  order neither person chose.
* Knobs are validated against a known set. A knob nothing reads is a dial that
  does nothing, which is worse than no dial.
* `policies/` gains the `get()`/`names()` registry that `srs/`, `graders/` and
  `presenters/` have had all along. `ARCHITECTURE.md` has listed `SessionPolicy`
  as an extension point since the beginning; it was the only one of the four
  without a protocol, and a second policy is the moment that stops being
  harmless.
* **A plan is an additional path, not a replacement.** Having one -- even
  marking it active -- does not alter what the Study tab serves. A session is
  built under a plan only when the request names one (`/api/session?plan=<id>`),
  and the designer's own practice is where that happens. The first version made
  an active plan take over the daily queue, and it was wrong for a plain reason:
  a learner who builds a plan, tries it and dislikes it should get their
  ordinary session back by clicking away from it, not by deleting the plan.
* An answer records a revision **only when it was given under a plan**. An answer
  from the Study tab is not evidence about any plan, and filing it under the
  active one would make every later comparison wrong -- which is the failure
  this column exists to prevent, arriving by a different door.
* **Practice is scoped to the plan's own material.** You asked to work on these
  topics, so the session serves those -- owed cards from them first, because
  answering something already owed is worth more than meeting something new, and
  then new material in the plan's mix. Padding a short session with unrelated
  cards would quietly turn "practise food and directions" into "practise
  whatever", which is the thing the designer exists to stop.
* **The debt is not the plan's to hide.** A scoped session is narrow by design,
  but everything it did not cover is still owed, still counted, and still served
  by the Study tab. That is the whole reason practice is a *second* path rather
  than a replacement: if a plan could make owed cards disappear, a learner would
  design around them once and meet them again a month later at four times the
  size.
* The allocation is a pure function, which is what makes
  `POST /api/plans/<id>/preview` cheap — and a preview is what makes tweaking a
  knob feel like an experiment rather than a commitment.

## What was rejected

**Implementing `tag_weights` as it stands.** It is per *course*, so it is the
author's opinion about the material rather than the learner's about their own
study, and it has no ordering — a dict of weights cannot be dragged. It stays as
a course-level default that a plan overrides.

**Letting a plan filter the queue.** Tempting, and it would make "only numbers
this week" trivial. Rejected: the debt is not the plan's to touch, and a learner
who could hide 200 owed cards behind a priority list would, once, and then find
them again a month later at four times the size.

> **Superseded on this point by
> [ADR-0018](0018-a-style-may-narrow-the-debt.md).** Not for a plan — a plan
> still cannot touch the debt, and the paragraph above still governs it. A
> *study style* (ADR-0017) may, because it expires by default, is visible from
> the Study screen even when it is hiding nothing, and cannot reach `owed_count`
> at all. The reasoning above was about a thing you activate and stop thinking
> about; the guardrails there are aimed squarely at that.
