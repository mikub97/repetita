# ADR-0008: The management surface sees everything

**Status:** accepted, 2026-09-11
**Context:** [ADR-0005](0005-the-client-never-sees-a-card-id.md),
[ADR-0006](0006-the-database-owns-the-material.md), the Manage tab

## Context

ADR-0005 established that the client never sees a card id, because ids are
authored from the material: a note teaching *obrigado* is called `obrigado`, and
**167 of 676 cards — a quarter of the corpus — had an id that was the answer.**
CLAUDE.md rule 3 states the companion invariant: answers must not reach the
client while a question is open, and `public_card` is the single path that
serialises one.

The Manage tab cannot work under either rule as literally stated. You cannot fix
a typo in an answer you cannot see, and you cannot edit a specific exercise
without naming it.

## Decision

A management API that returns note ids and every field, answers included. It is a
sibling of `revealed` — *everything* — not a variant of `public_card`'s filter.

## Why this is not the leak ADR-0005 prevents

The two situations differ in the thing that made the original a bug.

The leak ADR-0005 closed was **passive and invisible**. A learner answering a
question had the answer sitting in the page, in a field they never asked for,
with nothing on screen to suggest it. No amount of good intent avoided it; you
would see it by opening the network tab, or by a stray autocomplete.

A management screen is neither passive nor invisible. Opening it is a deliberate
act with an obvious purpose, and looking up an answer there to avoid answering
honestly is not a leak — it is choosing not to study, which no engine can or
should prevent. The same learner can already read `courses/*.yaml`.

So the rule is kept where it does work, and only there:

* `public_card` remains the **only** path that serialises an open question, and
  its field filter still comes from `NoteType.visible_before` rather than from
  anything written beside it.
* The raw-bytes leak tests over `/api/session` and `/api/state` are not relaxed
  by a byte. `tests/web/test_manage_api.py` adds one asserting the session still
  addresses cards by handle after material has been edited.
* Management routes never appear in a session payload and are never called by
  the study loop.

## Consequences

* **The quarantine had to move with the material.** `validate.check` refuses a
  note that gives away its own answer, and it ran only in the loader, over notes
  fresh out of a file. Once the database is what gets served — see the ADR-0006
  amendment — an edit reaches a learner without passing it. `build_library` now
  runs it over whatever the database holds, however it got there. Without that,
  this ADR would be trading a passive leak for an easier one.
* **A quarantined edit is named, not hidden.** `POST /api/material/confirm`
  applies the change and reports which notes cannot be served. Refusing the edit
  would strand half-finished work with nowhere to live; applying it silently
  would make an exercise vanish from the course with nothing said.
* **Two things are refused outright.** A note `id` cannot be edited — it is a
  scheduling key, and renaming one deletes a learner's progress on that item with
  nothing in the app to show for it (rule 1). `repetita check-ids` only diffs
  committed YAML in a pull request, so it would notice long after the damage, and
  the guard therefore lives in the write path. A `notetype` cannot be edited
  either: it decides which cards exist, and dropping one leaves the history
  behind it unreachable.
* **Deletion is archiving.** Same reasoning as ADR-0006, and the same reason
  there are still no foreign keys.
* If this surface ever became reachable by someone who is not the author — a
  shared deployment, a classroom — it stops being safe, because the argument
  above rests entirely on the reader being the owner of the material. That is the
  condition to re-examine, not the field list.
