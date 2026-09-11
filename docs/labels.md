# Names

Every exercise has a short name. The board shows it, the confirm drawer says
what is about to change by it, and the plan preview lists a few of them. Before
this existed all three printed the note id — `gram-atras-passado-ainda.tempo-01`
— which is a directory name and a sequence number, and tells you nothing about
what the exercise is.

```
atrás              a name
morava             a name
Nós trabalhamos…   a name, truncated
```

## Where it comes from

**The answer**, trimmed to 20 characters at a word boundary. Whatever the note
type declares as its `expect` field — nothing here knows any language, it reads
the field the course names and counts characters.

If there is no answer to read, the family key (`morar` from the cue
`morar — imperfeito, eu`); if there is no family either, the part of the id
after the unit prefix. Both are floors rather than choices: a row with no name
at all is worse than a row named awkwardly.

The obvious rule — the cue first, since it is already parsed as the family key —
was measured and lost, twice over:

| rule | median length | longest | repeats within a set |
| --- | --- | --- | --- |
| cue head first | 10 | 72 | 39% |
| **answer first** | **6** | **20** | 20% |

which is not surprising once said out loud: a family key is *designed* to be
shared. Six forms of `treinar` have one between them. It names a family, not an
exercise.

## The thing most likely to be misread

**A name is a name, not an identifier.** Names repeat, legitimately: in
`licao-2026-09-06-genero` twenty exercises are about grammatical gender and
their answers are `o` and `a`. Twenty rows reading `o` is the problem this was
meant to solve, so the board handles it — **where a name appears more than once
in a column, that row shows the id suffix beside it**:

```
o · genero-cinema
o · genero-problema
a · genero-viagem
```

and only there. The suffix is unique within a set by construction — every id is
`<unit>.<rest>` — so it always separates them. What the board does **not** do is
make the stored name unique, which would mean inventing text nobody wrote.

If you need to know exactly which exercise, the id is still there: hover a row,
or open it.

## Editing one

Type a name in the inspector, next to the tags. That **pins** it: `label_custom`
goes to 1, and from then on editing the exercise's answer leaves the name alone.
Derived names follow the answer; typed names do not, because the only reason to
type one is that the rule got this one wrong.

A pinned name is exported to `courses/*.yaml` as `label:` on the note, and read
back on import. A derived name is not written to the files: it would come back
identical from the rule, so it is not content, and writing it would add a line to
every note in the course that nobody authored and everybody would have to review.

One consequence worth knowing: since a name is not part of a note's content hash,
editing `label:` in a file **and nothing else** is not seen as a change by the
next import. Change the note, or type the name in the app.

## Where it lives

* `src/repetita/content/labels.py` — the rule, a pure function.
* `notes.label`, `notes.label_custom` — the storage, migration 6.
* Maintained on import (`store/cards.py`) and on edit (`store/material.py`), and
  backfilled at startup for anything missing one.
