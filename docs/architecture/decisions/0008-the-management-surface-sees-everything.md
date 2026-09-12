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

## Amendment, 2026-09-11: the plan preview lists names

The Design tab's preview could say *how many* exercises a plan would introduce
and never *which*, which made it hard to believe: a plan is a claim about what
you will study, and a screen that answers it with bar charts is asking to be
taken on trust. It now lists a shuffled sample of names — at most twelve.

**A name is usually an answer.** It is derived from the note type's `expect`
field (see [labels](../../labels.md)), so for the 639 `gap` exercises in the
course this was built for, the name *is* the word you would have to produce. The
preview is one click from practising. So this is a real loosening, in the same
family as the decision above and accepted for the same reason: the person
reading the Design tab owns the material and can already read `courses/*.yaml`.

**Shuffling is a mitigation, not a fix, and the difference matters.** What the
shuffle removes is the correlation between the order you read and the order you
are served: without it the preview would show the first five introductions in
sequence, and reading the screen twice would be studying them. With it, you have
still seen answers — nothing about a shuffle unsees them. If that is not
acceptable in some future deployment, the fix is to drop the names, not to
shuffle harder.

What has not moved:

* `public_card` is still the only path that serialises an open question, and the
  Study path is untouched.
* **No card id crosses the wire.** ADR-0005 stands exactly as written; the
  preview sends names and counts. `tests/web/test_designer_api.py` asserts this
  over the raw response bytes, and asserts that two calls differ in order.
* The sample is capped. Twelve is enough to recognise what a plan is about and
  too few to be a study list.

## Amendment, 2026-09-12: the condition named above has arrived

The last consequence in the list above reads:

> If this surface ever became reachable by someone who is not the author — a
> shared deployment, a classroom — it stops being safe, because the argument
> above rests entirely on the reader being the owner of the material. **That is
> the condition to re-examine, not the field list.**

It has arrived. There are four accounts now ([ADR-0016](0016-accounts-and-whose-history-it-is.md)),
and Karola can open Manage and read the answers to Radek's material.

**Re-examined, and the decision holds — with its reason replaced.** The old
argument was *the reader is the owner*. That is no longer true. The new one is
narrower and has to be stated rather than assumed:

**These four people are each other's teachers.** Every account may write and
manage material, and the sets in the English course are already mixed together —
Karolina's, Radek's and Małgosia's, in one course, filed by a `tutor` axis. A
management surface that hid other people's exercises would hide most of the
course from most of the people maintaining it, and the whole point of one shared
database rather than four is that they are maintaining it together.

So: **visible, not editable.** Ownership is enforced on the write path in
`store/`, and reading stays exactly as this ADR describes.

**What this does not cover, and would need re-examining again:** a deployment
where the accounts are strangers to each other — a classroom, a public server,
anything where somebody signs up rather than being added by name at a command
line. There the argument above is simply false: a student can open Manage and
read the answers to tomorrow's test. The mechanism that would be needed is not a
smaller field list but a second surface, and this ADR should be read as not
applying rather than as stretched to fit.

**What has not moved, again:**

* `public_card` is still the only path that serialises an open question.
* No card id crosses the wire. ADR-0005 stands exactly as written.
* **Nobody's progress reaches anybody else.** That is new and is the sharper
  line now: material is shared and `card_state` is not. `tests/web/
  test_two_people.py` asserts it over the raw response bytes for the state, the
  session, the board and the catalogue — the same way this file's leak tests
  assert the answer rule, and for the same reason: a field nobody remembered to
  look at is exactly the field that leaks.
