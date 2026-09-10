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
* A plan cannot excuse you from review. It decides which **new** material is
  woven in and at what share; every owed card is still owed, in the same order.
  A plan that could defer the debt would be a way to avoid the only part that
  demonstrably works.
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
