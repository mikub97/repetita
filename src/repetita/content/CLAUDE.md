# CLAUDE.md — src/repetita/content/

Loading and validating course files. The important job of this package is **not
parsing** — it is refusing to serve broken material.

## The rule this package exists for

An exercise must never contain its own answer.

The bug this was built around was a hint reading `fim de semana = weekend` for
the answer `fim de semana`: the exercise handed over its own solution. It
happened because `prompt` and `hint` were each doing two jobs — posing the task
*and* restating the rule. Here those roles are split across declared field sets:

* **visible before answering:** `prompt`, `cue`, `hint`, `situation`,
  `translation`, `instruction`
* **visible after answering:** `answers`, `explain`, `target`, `source`

An item whose answer appears in a before-field is **quarantined**: dropped from
the pool entirely, listed in the UI, and reported by `repetita validate` with a
non-zero exit. Not a console warning — the predecessor had one of those, and 70
of 330 items leaked anyway.

If you add a field, you must decide which set it belongs to. There is no
"neither".

## Rules

1. **Quarantine, never coerce.** When YAML hands back something of the wrong
   type, refuse it with the fix spelled out. Do not stringify it. YAML 1.1 reads
   `no`, `yes`, `on`, `off` as booleans, and `no` is a perfectly ordinary answer
   in several languages — silently coercing `False` creates an item whose correct
   answer is the text `"False"`.
2. **Accent comparison is accent-SENSITIVE.** `esta` and `está` are different
   words. Folding accents before looking for a leak buries real leaks under a
   pile of false positives. An accent-only match is a warning; the item still
   serves.
3. **Item ids are scheduling keys.** Never derive one from the prompt text, and
   never change one by editing a file: editing a sentence must not orphan its
   history. When an id does need to change, `repetita rename-id` moves the
   history across nine tables and records the rename — see ADR-0011.
4. **A malformed value is refused, not ignored.** A bad `lesson:` date silently
   dropped would push the whole pack to the back of the introduction order —
   which looks exactly like "the app is ignoring today's lesson".
5. **One implementation of every rule.** The CLI and the running app must not be
   able to disagree about what is safe to serve. Never write a second, "quicker"
   check.

   The mechanism moved. It used to be that `validate` called the same loader the
   app did — true until ADR-0006, and then not: the app serves the database, and
   an exercise written or fixed in the app passes through no loader on its way to
   anybody. So the shared thing is now `validate.check` itself, called from three
   places — the loader, `web/app._servable` on every build, and
   `repetita validate --db`. The last two are what cover material this package
   never sees.

## Layering

May import: `core`, pydantic, yaml. Must not import: `store`, `web`.
The loader produces plain objects; persisting them is someone else's job.

That is why `load_fragment` takes its exercise types and facets as arguments
rather than reading them: a fragment has no `course.yaml`, the course it belongs
to is in the database, and this package may not look there. The caller fetches
them and hands them over.
