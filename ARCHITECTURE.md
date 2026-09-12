# Architecture

Repetita is a spaced-repetition engine that knows nothing about any particular
language, plus courses that supply all the language-specific knowledge.

## The shape of it

```
  course YAML                                       the Create tab
  (reviewed, CC BY-SA)                              (written here)
        |                                                 |
        |  repetita import / the Import panel             |
        \------------------>  DATABASE  <-----------------/
                                 |  |
         repetita export /       |  |
         the Export button  <----/  |   notes, cards, units, facets,
         back to course YAML        |   exercise types -- owned, merged,
                                    |   archived, never rebuilt
                                    v
      notes         one atom of knowledge, typed fields
        |
        |  card templates from the note's exercise type
        v
      cards         (note, template) -- THE UNIT OF SCHEDULING
        |                                   |
        |  presenter                        |  scheduler backend
        v                                   v
      form                              due date
   typein / choice / wordbank        sm2 | fsrs6 | leitner
   flashcard / match / speaking
        |
        v
     grader  ->  Judgement(rating, diff)  ->  review_log (append-only)
```

The database is in the middle of that picture and not beside it. Files are one
of two ways material gets in and the only way it gets out for review; nothing
reads them unless somebody asks ([ADR-0015](docs/architecture/decisions/0015-yaml-is-a-way-in-and-a-way-out.md)).

Read [ADR-0001](docs/architecture/decisions/0001-note-card-form.md) for why note,
card and form are three things rather than one.

## Layers, and what may import what

| Layer | May import | Must not |
| :-- | :-- | :-- |
| `core/` | stdlib only | anything else in `repetita` |
| `srs/`, `graders/`, `presenters/` | `core` | `store`, `web`, `content` |
| `content/` | `core`, pydantic, yaml | `store`, `web` |
| `policies/`, `difficulty/` | `core`, `srs` | `web` |
| `store/` | `core`, `content` | `web` |
| `web/` | everything | — |

The direction of that table is the whole design. `srs/` cannot reach a database
or a clock, which is what makes it testable in isolation — and the scheduler is
the one place where a subtle bug costs months of study before anyone notices it.

## Four extension points

Each is a `Protocol` in `core/protocols.py` with a registry, so adding one is a
new file plus a registry entry — never a change to the core.

* **`SchedulerBackend`** — when should this card come back?
  ([ADR-0003](docs/architecture/decisions/0003-scheduler-is-a-plugin.md))
* **`Grader`** — is this answer right, and how wrong is it?
* **`Presenter`** — which form should this card be asked in right now?
* **`SessionPolicy`** — what goes into today's queue?

## Two things that are separate on purpose

**When to ask, and how hard to ask.** Duolingo keeps spacing and difficulty
targeting in different models, and it is right to. A system that conflates them
reviews at the right moment but always at the same level. `srs/` answers *when*;
`difficulty/` answers *at what level*.

**Content and progress.** The database **owns** `notes` and `cards`;
`courses/*.yaml` is an import/export format, not the source of truth. An import
merges — a note edited in the app survives it, and one that has left the files is
archived rather than deleted. That is ADR-0006; ADR-0010 let material begin life
in the app, and ADR-0015 stopped startup importing the files behind everyone's
back. `card_state` is never rebuilt by any of it. So fixing a typo in a sentence
costs nothing — an earlier design keyed tracking on the prompt text itself, which
meant editing a sentence silently orphaned months of history.

## Invariants

These are not style preferences. Each one exists because breaking it caused a
specific, expensive problem.

1. **An item id is a scheduling key and is immutable.** Renaming one silently
   deletes a learner's progress on it, with nothing in the UI to reveal it. CI
   enforces this against `main`.
2. **Answers are not in the payload while a question is open.** Not hidden by
   CSS, not filtered client-side — absent. `public_card()` is the single
   serialisation path so the rule cannot be true in one view and false in
   another.
3. **An exercise that contains its own answer is quarantined, not warned about.**
   The predecessor of this app shipped a console warning for this, and 70 of 330
   items leaked anyway.
4. **The engine contains no target language.** Accent folding happens because a
   course asked for it, not because the engine knows about Portuguese.
5. **Every answer is logged, with the state that preceded it.** It is the only
   thing that makes switching or tuning a scheduler possible later, and it cannot
   be reconstructed after the fact.
