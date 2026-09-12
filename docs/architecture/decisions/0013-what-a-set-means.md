# ADR-0013: What a set means

**Status:** accepted, 2026-09-11
**Context:** [ADR-0001](0001-note-card-form.md), [ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0010](0010-material-can-be-written-in-the-app.md), `docs/labels.md`

## Context

The complaint that started this was *"the sets are chaotical and I don't have a
proper way to manage the sets and exercises I added with agent"*. A design review
took it apart and found the cause is in the data, not the CSS. Measured on the
live course:

* **31 sets, every one with `title = '{}'`.** No `unit.yaml` exists anywhere in
  the private course, so the board prints 31 slugs, most truncated at about 22
  characters: `gram-demonstrativos…`, `licao-2026-09-06-ge…`.
* **Every exercise name is derived from its answer** — `label_custom` is 0 on all
  757 — and answers repeat. **160 of 757 exercises (21%) sit under a name shared
  with a sibling in the same set.** One set is 15 rows called `o` and 5 called `a`.
* **Four other axes are fully populated and none is reachable from the board.**
  `track` and `level` cover all 757 notes, `topic` 626, `source` 200, `lesson`
  251. `/api/catalogue?group_by=<axis>` already serves them; `designer.js`
  assigns `catalogue.axes` to a variable **nothing ever reads**.
* **`notes.origin` is serialised on every `/api/material` call and no JS reads
  it.** `edited_at` is not in the payload at all. So "material I wrote", "material
  an agent wrote" and "material I changed afterwards" look identical.

Four concepts already exist in the data. Three of them are invisible. The fix is
not a fifth concept; it is to say once what each is for, and make each visible in
exactly one place.

## Decision

| Concept | The question it answers | Cardinality | Where it lives |
| --- | --- | --- | --- |
| **set** (`unit`) | *where does this exercise live?* | exactly one | the shelf: Manage's columns, Create's picker |
| **topic / tag** | *what is it about?* | many | Design's planning; a filter on Manage |
| **lesson** | *when did it arrive, and with what?* | one date, optional | an arrivals view; a badge |
| **origin / edited** | *who wrote it — a file, an agent, or me?* | one | a mark on every row |

### Three rules

1. **A set is a shelf, not a subject.** It is where a thing is filed, chosen by
   whoever filed it. It is not a claim about what the thing teaches — that is
   what tags are for. This is already true in the data: `gram-preterito-perfeito`
   holds notes from three files and two lessons. Saying it out loud settles the
   "should this be a set or a tag" question for good, and both the Italian course
   and the Anki import will ask it.
2. **Every set has a name a person wrote, and may have a description.** A slug is
   an identifier, not a name. Where no name exists the interface says so and
   offers to take one, rather than silently printing the id. The description is
   for what the name has no room for — what the set is for, what it assumes —
   and is edited in the same place.
3. **Every exercise says where it came from.** Not as an audit trail: as one mark
   in the row, so the three provenances are three visibly different things.

### Set-level operations live in one place, under one rule

Renaming a set was in Create, which saves immediately; removing one was in
Manage, which stages until Confirm. Two operations on a set's existence, in two
tabs, under opposite commit models — the seam a new user falls through.

**Both now live in Manage and both are staged.** You look at sets in Manage, so
you name them there. The commit-model split between the tabs stays (ADR-0010
argued it, and it is labelled on both), but it no longer cuts through a single
concept.

## What this does not change

* **`label` keeps deriving from the answer, and names still repeat.**
  `docs/labels.md` is right that this is deliberate: for the study loop a name is
  a name. The board's problem is not that names repeat, it is that it printed a
  two-digit id tail to tell them apart — which identifies nothing to a reader.
  The row now shows the *cue* where one distinguishes the rows, and falls back to
  the id tail only where nothing else does. **A second field, not a changed one.**
* **Archiving stays the default.** Making archived material reachable again is
  the other half of ADR-0006's promise and is Tier 2 of the review, not this ADR.

## Consequences

* `units` gains a `description` column (schema 8) and `Unit` a `description`
  field, both i18n dicts like `title`.
* `_note_json` gains `edited_at`; `origin` was already there and unread.
* A nameless set is rendered as unfinished rather than as named-in-slug, which
  makes rule 2 self-enforcing: the interface asks until someone answers.
* The 31 live sets get titles and descriptions written into `unit.yaml`, so the
  loader has them and the export round-trips.
