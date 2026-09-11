# ADR-0009: Material is captured before it is shaped

**Status:** accepted, 2026-09-11
**Context:** [ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0008](0008-the-management-surface-sees-everything.md), the Manage tab's
**Capture a lesson** button

## Context

Every other route into this system demands a finished exercise. A YAML file has
to parse, satisfy `Note`'s `extra="forbid"`, name a note type that exists, and
survive `repetita validate`. The Manage tab is gentler but asks the same thing:
which exercise, which field, what value. Both assume you already know what the
exercise is.

That is not the state a lesson arrives in. What exists an hour after a lesson is
half a page of notes in two languages: a heading with a date, a rule half
written down, three examples, two words whose gender surprised you, and a
sentence the teacher said that you want to keep. It is not six exercises yet, and
deciding *which* six it is — which are gaps, which are transforms, what the cue
should say — is a different activity, done in a different state of mind, usually
on a different day.

Forcing the two together loses the note. The lesson is written down where it can
be typed quickly, which in practice is a text file, a phone, or nowhere; and the
material never reaches the course, which is the failure mode this is actually
about. An import that *guessed* the exercises would be worse: a guess made here
is a wrong exercise discovered weeks later, mid-session, with a schedule already
attached to it.

## Decision

A queue for raw material, written to on purpose and parsed by nothing.

`material_drafts` holds exactly what was typed, with the time it arrived, and
later what came of it. The **Capture a lesson** button is a blank box. Nothing
validates it, classifies it, or reformats it. Shaping it into exercises is a
separate, deliberate step done by an agent — `repetita inbox` to read the queue,
the `repetita-licao` skill to write the course material, and the result reviewed
as a normal diff in the Manage tab before it is confirmed.

## What the queue is not

Said explicitly, because the table sits next to `notes` and will be mistaken for
it:

* **It is not material.** Nothing in it is in any course.
* **It is not studied.** No card, no schedule, no `card_state` row.
* **It is not counted.** It appears in no catalogue, no plan, no statistic.
* **It is not validated.** The leak rule, the id rule and the type rules all
  apply to the exercises made from a draft, and none of them apply to the draft.
* **It is never silently converted.** A draft becomes exercises only when
  somebody asks for it, and the exercises are reviewed before they are confirmed.

## Consequences

* **The body is kept after processing, not deleted.** A draft is the provenance
  of whatever was written from it, and the thing to re-read when one of those
  exercises turns out wrong — which is exactly the moment deleting it would
  hurt. `discard` exists for the other case: a note captured by mistake, which is
  provenance for nothing.
* **Closing a draft records what came of it.** `repetita inbox --done <id>
  --note "…"` — an outcome, in a sentence. A closed draft with no note answers
  "where did this exercise come from" no better than a deleted one.
* **This module breaks the house style on purpose.** Everything else under
  `store/` refuses what it cannot understand; `drafts.py` accepts anything. The
  docstring says so, so that the next person to read it does not add validation
  to be helpful.
* **The handover is a CLI, not a background job.** Nothing watches this queue.
  An agent reads it when asked, the same way it reads `repetita issues`. A queue
  that emptied itself would be a guesser with extra steps.
* If the queue is never read, nothing is lost except the time spent typing into
  it — the failure mode is a backlog, which is visible on the button, rather
  than silently wrong material.
