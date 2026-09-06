# ADR-0001: Content is a note, scheduling is per card, presentation is a form

**Status:** accepted, 2026-09-06

## Context

In the app this was extracted from, one YAML entry was one question in one form.
`type: gap` stated simultaneously *what the knowledge is* and *how it is asked*.

Three things follow from that conflation, all bad:

* To test the same word by recognition, by production and by listening, you write
  it three times. Content cost scales with the number of exercise types.
* Scheduling state is per item, so "I recognise *saudade* but cannot produce it"
  is one number, and it is the wrong number for both facts.
* `ordem` (rebuild a scrambled sentence) had to be declared a *type*, validated
  as a type, and then generated at runtime by a hardcoded ladder — because it is
  not a type at all, it is a way of asking.

## Decision

Three layers, named separately:

* **Note** — the atom of content. Typed fields, written by an author.
* **Card** — `(note, template)`. A direction of testing. **The unit of
  scheduling.** One `vocab` note yields `recognize`, `produce`, and — only if the
  note has an `audio` field — `listen`.
* **Form** — how a card is asked *right now*: `typein`, `choice`, `wordbank`,
  `flashcard`, `match`. Chosen by a presenter, not fixed by the content.

Card templates are declared in `notetypes.yaml`, per course. A template with a
`requires:` clause generates no card when the field is absent, so optional
content degrades instead of erroring.

## Evidence

Three independent systems arrived at the same split:

* **Anki** — `cards.ord` is *which template of the note*; `CardRequirement` gates
  generation on non-empty fields. Adding a template retroactively gives every
  existing note a new card.
* **quenti** (Quizlet clone) — `StudiableTerm`'s primary key is
  `(userId, containerId, termId, mode)`. Progress is per *(item × mode)*, never
  per item.
* **LibreLingo** — exercises are **generated, never authored**: one vocabulary
  word produces a multiple-choice card, a typed card and a listening card, each
  documented with what it is generated from and which properties it uses.

## Consequences

* `ordem` stops being a type and becomes the `wordbank` form. The runtime ladder
  in `render_as()` becomes one presenter among several.
* **Sibling burying becomes mandatory.** Three cards from one note in one session
  is near-worthless as evidence, and the model makes that situation common rather
  than rare.
* Content authored under the old model must be mapped on import; the mapping is
  in `docs/migration.md` and is lossless.
* A content PR is worth more than it looks: adding 24 notes can add 72 cards. CI
  reports that delta on the pull request so a reviewer sees the real impact.
