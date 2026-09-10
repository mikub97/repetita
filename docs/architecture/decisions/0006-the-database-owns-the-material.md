# ADR-0006: The database owns the material

**Status:** accepted, 2026-09-10
**Supersedes:** the content half of CLAUDE.md rule 2 — *"Content is a cache and is
wiped and re-derived on every load"*
**Does not touch:** the progress half of the same rule — *"`card_state` is never
rebuilt from content. Study history is not, ever."*

## Context

`courses/*.yaml` was the source of truth and `notes`/`cards`/`distractors` were a
cache. `sync()` opened with `DELETE FROM distractors; DELETE FROM cards;
DELETE FROM notes` and re-inserted everything on every load. `importers/emit.py`
said as much in its docstring: writing only to those tables "writes to the wrong
place -- the next startup discards it".

That was the right shape while the only thing anyone did with material was author
it in a text editor and study it. It stops being the right shape as soon as the
material has to be *worked with* inside the app: grouped, re-tagged, re-prioritised,
and — next — added to and edited.

The specific thing it blocks: a "design my lessons" screen, where topics are
dragged into a priority list and the algorithm serves material accordingly. That
needs the material to carry structure that survives a reload, be queryable by
that structure, and eventually be editable in place. None of the three is
possible against a table that is thrown away on every startup.

## Decision

The database owns the material. `courses/*.yaml` becomes an import/export format.

`sync()` merges. Four outcomes per note:

| At the source | Here | Result |
| :-- | :-- | :-- |
| present, new | absent | inserted |
| unchanged | anything | left alone (un-archived if it had gone) |
| changed | untouched | updated |
| changed | **edited here** | **conflict** — the local note stays, the id is reported |

A note that has left the files is **archived**, never deleted. Archived material
leaves the queue and stays on the row.

The conflict rule is the load-bearing one. Neither version is lost and neither is
silently chosen. Picking a winner automatically is the only behaviour that would
make an import unsafe to run, because the loser is unrecoverable and nothing in
the UI would say so.

## Why archive rather than delete

Deleting orphans `card_state` rows whose `card_id` no longer resolves to
anything. Under the old design that was survivable — the content was rebuilt from
the files a moment later, so a deletion was never really a deletion. Once the
database is the owner, a delete is final, and the study history behind it cannot
be reconstructed from anything.

**There are still no foreign keys.** The schema has never had one, precisely so
that `card_state.card_id` survives a note disappearing. Ownership makes that
reasoning stronger, not weaker: a note removed in the app must never cascade into
someone's schedule. An FK with `ON DELETE CASCADE` would turn a content mistake
into permanent data loss, and one without a cascade would turn it into an error
nobody can clear.

## The property that used to be free

*"A card whose note vanishes keeps its history."*

Under wipe-and-rebuild this held because nothing linked the tables — it was a
property of the **absence** of a mechanism. Archiving replaces that absence with
an actual mechanism, and a guarantee that was structural becomes one that has to
be maintained.

That is exactly how a safety property is lost in a refactor: every existing test
still passes, because the tests were written against the old mechanism. So it is
re-pinned deliberately here — `TestOwnership` asserts that a removed note's row
survives, that its `card_state` survives, that material which comes back returns
on the schedule it left with, and that no sequence of imports changes the row
count in `review_log`.

## Consequences

* Every query over content must exclude archived rows or archived material stays
  in circulation. Two did: the session queue (`policies/daily.py`) and
  `card_ids()`. `lesson_first_seen_on` deliberately does not — it counts answers
  that really were given, and an answer is not retracted by its note leaving the
  course.
* `sync()` returns a `SyncReport` (`added`/`updated`/`archived`/`conflicted`)
  rather than a bare pair of counts. A merge that cannot report a conflict is a
  merge that hides one.
* `content_hash` covers the note *as authored* and deliberately excludes
  `origin`. Moving a note to another file is not a change to the material, and
  hashing the path would turn any reorganisation of a course into a wall of
  conflicts.
* `distractors` are still rebuilt wholesale. They are derived from the material
  rather than authored in it, nobody can edit one, and they are deterministic, so
  there is nothing for a merge to protect.
* **Export is now owed.** The README promises that "adding a lesson is a pull
  request a non-programmer can make", and once material can be edited here that
  promise depends on being able to write it back out. Export is not in this
  change; it is the next one, and shipping app-side editing before it would
  quietly break the claim.
* `importers/emit.py`'s docstring is now wrong and is corrected.
* The engine still contains no Portuguese and no Polish. Nothing here is
  language-specific.

## What was rejected

**Keep YAML authoritative and add a derived index beside it.** This was the
initial proposal, and two reviewers independently argued for it: it preserves the
invariant, and grouping by type/state/topic is reachable without touching
ownership. It was rejected for one reason — it does not survive the next step. As
soon as a note can be *created* in the app, there is no file for it to be derived
from, and the index has to become the owner anyway. Doing it then means doing it
twice, with a body of code already built on the wrong assumption.

**A `--force` flag that resolves conflicts by taking the file.** A conflict is
rare and interesting; a flag that makes it disappear will be reached for
routinely, and then the interesting cases go with it.
