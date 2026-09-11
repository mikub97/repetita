# ADR-0011: The rules become mechanisms

**Status:** accepted, 2026-09-11
**Context:** CLAUDE.md, [ADR-0004](0004-imported-history-is-not-corrected.md),
[ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0010](0010-material-can-be-written-in-the-app.md)

## Context

This repository accumulated about seventy rules across nine documents, and the
shape of nearly all of them was **prohibition**. The word *never* appeared eleven
times in the root `CLAUDE.md` alone, and the sharpest of them — *never change an
existing item `id`* — was repeated in **twelve** places: two `CLAUDE.md` files,
`CONTRIBUTING.md`, the PR template, two skills, four documentation pages, a CI
job and the write path itself.

Every one of them had a real reason. The id rule protects the only thing here
that cannot be reconstructed: 511 answers and 105 card states, which come back
from nowhere if they are lost. Material comes back from `courses/`; a schedule
does not.

But a prohibition is a strange way to protect something, because it has no idea
what it costs. Three costs were being paid daily:

* **An id derived from a typo stayed wrong for ever.** Since ADR-0010 an id is
  made from the answer at the moment an exercise is first saved. Fix the answer a
  minute later and the id keeps the typo, permanently, with the sanctioned
  response being to leave it.
* **Rubbish accumulated with no way out.** Material could only be archived. A
  test set made by accident, a duplicated import — all of it stayed.
* **Small fixes became issues.** *"Do that, and nothing else. If you find a
  second bug, open an issue rather than fixing it"* sent three one-line fixes to
  the tracker in a single afternoon, where they sat.

## Decision

**A prohibition is replaced by a mechanism wherever one can exist.** The rule
stops being *never do X* — which nothing can enforce and which has no way to
weigh what it costs — and becomes *X is done by this command, which does it
safely and says what it did*.

Four rules remain absolute, and they are the ones where no mechanism helps:
study history is never destroyed silently; answers do not reach a client while a
question is open; the scheduler is pure; the engine knows no language. Licensing
is separate again — not a rule about care but the condition for the repository
existing, and the one mistake that cannot be removed from git history.

### The safety net came first

`repetita snapshot` / `restore`, taken through SQLite's backup API rather than
`cp`. Without it, everything below would be loosening a rail with nothing
underneath. With it, a mistake is a restore.

It also fixed something that was already wrong: `data/` held seven hand-made
backups, taken with `cp` against a database in WAL mode. That is a race — the
committed rows sit in the log until a checkpoint — and although all seven happen
to have won it, the losing side is a backup that opens cleanly and is quietly
missing the last answers.

### What each prohibition became

| was | is |
| --- | --- |
| *Never change an existing item `id`* | `repetita rename-id`, which moves the note, its cards and its history across **nine tables and ten columns** in one transaction, and records the rename in `courses/<course>/renames.yaml` |
| *Archived, never deleted* | `repetita purge`, which reports what it would orphan before it acts, and needs a second flag before it will touch history. Archiving stays the default |
| *Writes go through `store/material.py`* | Raw SQL is fine; the four obligations that module discharges — `edited_at`, `content_hash`, re-expansion, `reclassify` — are what you owe if you go around it |
| *No direct pushes to `main`* | Commit to `main` for ordinary work; open a PR when the change earns one. `enforce_admins` was switched off and the four required checks kept |
| *Do that, and nothing else* | Fix what you find, and say so |

### CI had to learn the difference

A rename done properly looks exactly like an id disappearing, which is precisely
what `check-ids` exists to catch — so leaving that check alone would have made
the new command legal and still unmergeable, which is the same as not having it.

The rename is therefore *recorded*, and the check reads the record — accepting it
only when the new id is actually present, because a record pointing at nothing is
a claim rather than a rename. **The rule became enforceable in the process.**
*Never rename* was a request that nothing could check; *a rename is written down*
is checked by a CI job and read by a reviewer.

## Consequences

* **Three things still ask a person first**, and they are about consequence
  rather than permission: deleting study history, deleting material that is still
  in a course, and issues labelled `human-only`. Pushing and publishing are not
  among them.
* **Rule numbers moved.** Older ADRs refer to "CLAUDE.md rule 1" (ids) and
  "rule 2" (history). Those ADRs are records of what was decided then and are
  left as they were written; the rule they name is now the *one* absolute rule
  about history, plus a command for ids.
* **The id rule is stronger than it was, not weaker.** It used to rely on
  everybody reading it in twelve places; now the write path refuses, CI checks
  the record, and the only way to do it detaches nothing.
* **`purge` can still lose history if somebody means it to.** That is the point —
  it takes two flags and a snapshot, and it reports the number of rows first. The
  thing that was forbidden was not deletion, it was deletion nobody decided on.
* If this repository ever has contributors other than its author and their
  agents, the process half of this ADR is the part to revisit: direct commits to
  `main` and *fix what you find* are reasonable for one person with a fast CI and
  a snapshot, and less so for a queue of strangers.
