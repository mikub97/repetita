# ADR-0012: An exercise type is an extension point

**Status:** accepted, 2026-09-11
**Context:** [ADR-0001](0001-note-card-form.md),
[ADR-0010](0010-material-can-be-written-in-the-app.md), `CONTRIBUTING.md`

## Context

`CONTRIBUTING.md` has always promised that *"a new scheduler, grader, presenter
or session policy is a new file plus a registry entry. It should not require
changing anything else."* Four extension points, each a directory of small
modules with a registry in front.

The exercise type was the fifth thing of that kind and the only one that did not
work that way: six of them in a single dict in `content/notetypes.py`, reachable
only by editing the engine. The consequences had already been felt twice:

* The Create tab (ADR-0010) shipped with a type picker over a fixed list, and its
  plan put a type designer out of scope **in those words** — *"new note types mean
  writing Python today; making them course configuration is its own change with
  its own ADR"*. This is that change.
* A course that wants a kind of exercise nobody anticipated — dictation, a
  conjugation table, a minimal pair — had no route at all that did not involve a
  pull request against the engine.

## Decision

**A type is a declaration, plus optionally a view.** Nothing else.

* `content/notetypes/` is a package: one module per type, and a registry with
  `get` / `names` / `builtin`, in the same shape as `graders/` and `srs/`.
* A course may declare its own in `courses/<id>/notetypes.yaml`, merged over the
  built-ins when it loads. A declared name that matches a built-in replaces it
  **for that course only**, which is deliberate: a course that wants `gap` graded
  as a sentence should not have to invent a name to get it.
* `web/static/types/` is the same idea on the client: one view per type,
  registered by name, with the generic renderer as the fallback. A type only
  needs a view when the default reads badly.

## Declarations are now checked, which they never were

Nothing verified that a card template's `grader` existed. The name was looked up
at answer time, so a typo was a `LookupError` in front of a learner, on a card
they had just answered. That was survivable while the six were Python literals
somebody had typed carefully. It stops being survivable the moment a course can
write one.

So a declaration is refused, by name, when a card points at a field the type does
not have; names a grader that does not exist; names a form that grader cannot
judge; or when a type has no cards or a card has no forms. A broken type is
**dropped and reported rather than merged**: the notes using it would otherwise
be quarantined for a reason that is not their fault.

### Running those checks against the six found two real bugs

Both of the same kind — a form the card's grader cannot mark — and both
unreachable behind an earlier form, so nobody had ever met either:

* `phrase.say` offered a **word bank** to the `self` grader, which reads a
  numeric self-rating out of the payload and would have scored an assembled
  sentence AGAIN every time. This was already filed as an issue from reading the
  code; the registry's own checks are what confirmed it.
* `vocab.recognize` offered a **flashcard** to the `typed` grader, which would
  have compared the learner's 1–4 rating against the answer.

Both declarations are fixed. Recognising a word by self-assessment is a
reasonable exercise and would need a `self`-graded card of its own — a design
decision, not a missing form.

### And one check of mine that was wrong

*"A card's `expect` field must not be marked visible-before"* sounds obviously
right and would have forbidden the most common exercise shape there is: in
`vocab`, `l1` is the prompt for `produce` and the answer for `recognize`. A
field's `visibility` is the type's default and the card always wins —
`NoteType.visible_before` excludes a card's own `expect` unconditionally.

The built-ins refused the check, which is the argument for running new validation
against known-good declarations before trusting it. The reasoning now sits where
the check was.

## Consequences

* **The documentation is generated from the registry**
  (`docs/exercise-types.md`, `scripts/gen_types_doc.py`, checked in CI), and its
  examples are rendered by the app's own modules rather than drawn to look like
  them. A type that changes changes its page.
* **Nothing about an existing course changed.** The generic view is still the
  fallback, so all 757 live notes render as they did; the six declarations are
  byte-identical apart from the two fixes above.
* **A per-course type is not a per-course grader.** Graders stay in the engine:
  they are code, and a course declaring one would be a course shipping code.
  A declaration may only compose what exists.
* `src/repetita/modes/` — an empty directory the layout section has described
  since it was created as holding "one module per exercise form" — is now
  genuinely misleading, because the client has both `modes/` and `types/`. It
  should go, or be built.
