# The inbox

Where a lesson lands before it is exercises.

**Add material** in the Manage tab opens a blank box. What you paste into it is
kept exactly as typed, queued, and turned into exercises later — by an agent,
when you ask. Nothing is parsed at capture time. [ADR-0009](architecture/decisions/0009-material-is-captured-before-it-is-shaped.md)
is why; this page is the loop.

Like [Tagging](tagging.md), it is written to be actionable by an agent working
from a one-line request, as well as by a person.

## Capturing (you)

Manage → **Add material** → paste → **Queue it**.

Semi-structured is the point. This is a real draft:

```
lekcja 11.09 — futuro simples
  vou + infinitivo
  ex: vou estudar amanhã
       ela vai trabalhar no sábado
  nowe słowa z feiry: jaca, caju, umbu
  ! o/a — umbu jest "o"?
```

A heading with a date, a rule half written down, two examples, three words, and
a question you want to remember to ask. None of that is an exercise yet, and
deciding which six exercises it becomes is a different job done on a different
day. Type it and close the box.

The button shows how many are waiting. Nothing else happens until you ask.

## Shaping (an agent)

Ask for it in a sentence — *"take what's in the inbox and make exercises"* — or
run the loop yourself.

```
1. repetita inbox                                   # what is queued
2. repetita inbox --show <id>                       # read one in full
3. <the repetita-licao skill>                       # write the course material
4. repetita export <course> --to courses/<id>       # write YAML back out
5. git diff                                         # this is the review
6. repetita inbox --done <id> --note "made 6 gap exercises in licao-2026-09-11"
```

Step 5 is what keeps it reviewable: since [ADR-0006](architecture/decisions/0006-the-database-owns-the-material.md)
the database owns the material, so new exercises are a database write, and they
become something a person can review once they are exported back to ordinary
course files as an ordinary diff.

Step 6 is not bookkeeping. The outcome is what makes a closed draft answer
"where did this exercise come from" six weeks later.

## What the queue does not do

* Nothing in it is in any course, studied, scheduled or counted.
* Nothing in it is validated — the leak rule and the id rule apply to the
  exercises made from a draft, never to the draft.
* Nothing converts itself. A backlog is the failure mode, and it is visible on
  the button.

## If you are an agent doing this

**Do:**

* Read the whole draft before writing anything. The useful part is often the
  aside at the end — *"! o/a — umbu jest 'o'?"* is an exercise.
* Follow the course's own conventions: the note types it already uses, the tag
  vocabulary in `facets.yaml`, the id shape of its existing units. See
  [Tagging](tagging.md).
* Say what you made, in the `--done` note, in a sentence a person can check.
* Leave the draft open if you only did part of it, and say which part in the
  request you answer.

**Do not:**

* Edit the captured text. It is somebody's handwriting; it is provenance, not a
  document.
* Invent exercises the note does not support. Three words and a rule is three
  words and a rule — if the draft does not say what the answer is, ask.
* Close a draft without saying what came of it.
* Discard a draft. `--discard` removes the body, and is for the person who typed
  it into the wrong window.
* Touch a note `id`. Rule 1, not negotiable.

## Where it lives

* `src/repetita/store/drafts.py` — the queue. Deliberately the one module here
  that accepts unvalidated input.
* `material_drafts` — the table: body, when it arrived, when it was closed, and
  what came of it.
* `repetita inbox` — the CLI, with `--show`, `--done --note`, `--discard`, `--all`.
* `POST /api/drafts` — what the button calls.
