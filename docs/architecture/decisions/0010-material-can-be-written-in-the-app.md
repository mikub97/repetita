# ADR-0010: Material can be written in the app

**Status:** accepted, 2026-09-11
**Context:** [ADR-0001](0001-note-card-form.md),
[ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0008](0008-the-management-surface-sees-everything.md),
[ADR-0009](0009-material-is-captured-before-it-is-shaped.md), the Create tab

## Context

ADR-0006 made the database the owner of the material and `courses/*.yaml` an
import/export format. It stopped half way: the database owned notes it could not
create. Every write path in `store/material.py` keyed off a note that an import
had already put there — `fields`, `tags`, `unit`, `archive`, `restore`, `label` —
and the only `INSERT INTO notes` in the codebase belonged to the importer.

So of the routes material could take in, none of them was *writing an exercise*.
The Manage tab fixes one that exists. A YAML file needs an editor, a validator
and a restart. The inbox and the `repetita-licao` skill both delegate to an
agent, which is right for a lesson's worth of notes and absurd for one sentence
you thought of on the way home.

## Decision

A fourth tab that writes material, and the store function behind it —
`save_set`, which creates, edits and archives exercises in one transaction.

**Save applies.** This is the one place the app departs from "nothing happens
until Confirm", and the reason is that the rule does not apply here: staging
exists so that you can see what an edit will *replace*, and a new exercise
replaces nothing. The Save button is the confirmation. An exercise that is
edited rather than written goes the same way, because a set you are working on
is one thing and splitting it across two screens to apply half of it is worse
than the inconsistency.

## What had to be true for this to work

**`origin` says who wrote it.** An import archives every note it cannot find in
the files, and an exercise written here is in none of them. It is exempted by
having no `origin`, not by having been edited: a note that *came* from a file,
was edited here and has since left the files should still be archived, and that
has not changed.

This is the fourth time the same rule has been needed, and it is worth writing
down in one sentence because it will be needed a fifth: **whatever the app
creates or changes must record that it did, or the next import undoes it.**
Notes learned it as `edited_at`, cards as `held`, units as a guard on their
upsert, and now notes again for the ones that begin life here.

**Distractors had to come from the database.** They were derived from the notes
an import had just read, which meant an exercise written in the app contributed
none and received none — so a multiple choice chosen in the editor was accepted
and then quietly never served. They are now derived from the material as the
database holds it, which is what ADR-0006 made the material.

## An exercise may now say how it is asked

`notes.forms` — `{"<template>": ["typein", …]}`, NULL meaning "whatever the note
type says", which is the case for everything that came out of a file.

This is a real departure from ADR-0001, which put presentation on the card and
chose it per showing: *"the form is chosen per showing, so the same card can be a
typed answer today and a multiple choice next week"*. That is still true of every
exercise that does not say otherwise. What is new is that an exercise may
express a preference, because some of them genuinely have one — a sentence you
want built from a word bank rather than typed, a pair of words you only ever want
to recognise — and the alternative was a new note type per preference.

Per template rather than per note, because a form is a property of a card and one
note can have several: `vocab` makes three, and *recognise it* and *say it out
loud* are not the same question.

**A form is only legal with a grader that can mark it.** `self` reads a number
out of the payload — the learner's own rating — so `flashcard` belongs to it and
to nothing else; put a `typed` card in a flashcard and every answer is the string
"3", scored AGAIN, with nothing about the result looking wrong from the outside.
`GRADER_FORMS` in `web/serialize.py` is that rule, and the write path refuses an
override that breaks it.

Like `label`, `forms` is exported only when set and is deliberately **not** in
`_content_hash`: putting it there would change every note's hash at once and turn
every locally edited note into an import conflict. The known consequence is the
same as for labels — editing only `forms:` in a file is invisible to the import.

## Consequences

* **Ids are made, not typed.** `ids.propose` derives one from the answer and
  checks it against every id the database has ever held, archived ones included:
  an archived note still owns its history, and handing its id to a new exercise
  would hand over the history with it. The screen does not show them. They are
  permanent from the moment Save is pressed, and rule 1 applies to them exactly
  as it applies to an id in a file.
* **A staged edit to something just saved is dropped**, and Save says how many.
  It described a version that no longer exists, and the next Confirm would have
  put it back.
* **A leaking exercise is applied and named**, the convention `apply_pending`
  already uses: refusing would strand half-finished work with nowhere to live,
  and the quarantine already keeps it away from a learner.
* **The browser decides nothing about validity.** The leak rule is
  accent-sensitive and word-boundary aware; `POST /api/material/check` runs the
  real one on every keystroke rather than letting a second implementation exist.
* `courses/*.yaml` is now fully an export format. Material can begin life in the
  database, and `repetita export` is how it becomes a file somebody can review.
