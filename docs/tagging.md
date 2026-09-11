# Tagging

How a course's tags are organised, and how to change them safely.

This page is written to be actionable by an agent working from an issue or a
one-line request, as well as by a person. If you are about to retag anything,
read [Changing tags](#changing-tags) first.

## The model

A note carries **tags**: flat, free-form strings in its YAML.

```yaml
notetype: gap
tags: [A2, gramatica, preposicoes]
notes:
  - id: gram-preposicoes.em-01
    ...
```

`facets.yaml` in the course root says **which question each tag answers**:

```yaml
axes:
  level:
    values: [A1, A2, B1, B2, C1, C2]
    ordered: true
    max_per_note: 1
  track:
    values: [vocabulario, gramatica, musica, fala, texto]
  topic:
    catch_all: true
```

So `[A2, gramatica, preposicoes]` reads as *level A2, track gramatica, topic
preposicoes* — without any note being rewritten.

**The tags are the authoring surface and stay that way.** The predecessor had
`track`, `topic` and `level` as three separate columns, and the import into this
engine flattened them into tags deliberately, because *"a tag is the general form
of something true about this note that a course may weight or filter on"*
(`importers/hub.py`). An axis is a *reading* of that bag, not a replacement for
it. Reintroducing per-axis fields would reverse a decision that was made on
purpose.

## The axes

| Axis | What it answers | Convention |
| :-- | :-- | :-- |
| `level` | How hard is this? | CEFR. **Exactly one** per note (`max_per_note: 1`). |
| `track` | What *kind* of knowledge is it? | `vocabulario`, `gramatica`, `musica`, `fala`, `texto`. Usually one; more than one is allowed. |
| `topic` | What is it *about*? | `comida`, `preposicoes`, `ser-estar`. **Several are normal.** |
| `source` | Where did it come from? | `licao-setembro`. Optional. |

Axis names are course configuration, not engine concepts. The engine contains no
Portuguese and no Polish, and it does not know what `vocabulario` means — CI
checks this.

### Shape rules

* Lowercase, kebab-case, no spaces, no accents: `preterito-imperfeito`.
* **A note may carry many tags, and many per axis.** Material about ordering food
  in a market is genuinely both `comida` and `cidade`; forcing a choice loses
  real information. Only `level` is capped.
* **Never encode a level or a date in a topic.** `vocab-a2-setembro` is three
  statements crammed into one string, and none of them can be counted. Write
  `[A2, vocabulario, licao-setembro]`.
* **A unit directory is a topic, not a date.** `gram-ser-estar`, not
  `lekcja-14`.
* **A topic should be something you could want more of.** If you would never say
  "give me more of this", it is probably a `source`, not a `topic`.

### The catch-all

One axis may be `catch_all: true`. Tags that no axis lists land there. This is
why adding a topic is an edit to a *note* and not to `facets.yaml`.

Declared values always win over the catch-all, so promoting a value onto a real
axis reclassifies it everywhere with no note touched.

## Changing tags

### Renaming a tag is safe

This is worth stating plainly, because [CLAUDE.md](https://github.com/mikub97/repetita/blob/main/CLAUDE.md) rule 1 —
*never change an existing item `id`* — makes people rightly afraid to rename
anything under `courses/`.

**A tag is the exception.** Nothing is keyed on it: no scheduling state, no
`card_state` row, no review history. Renaming a tag cannot lose a learner's
progress the way renaming an id silently does.

What a rename *does* touch is `facets.yaml` and any study plan that prioritises
that value. `repetita tag rename` updates both and leaves an alias behind, so
material tagged before the rename keeps resolving:

```yaml
aliases:
  tempo-adverbios: tempo
```

### The loop, from an issue to a reviewable diff

```
1. repetita issues                                    # or the request you were given
2. repetita catalogue <course> --group-by topic,state # look before touching anything
3. repetita tag <verb> ... --dry-run                  # shows every affected note
4. repetita tag <verb> ...                            # apply
5. repetita export <course> --to courses/<id>         # write YAML back out
6. git diff                                           # this is the review
7. repetita issues resolve <id> --note "..."
```

Step 5 is what keeps the whole thing reviewable: since ADR-0006 the database owns
the material, so a tag change is a database write — and it only becomes something
a human can review once it is exported back to normal course files, as a normal
diff, in a normal pull request.

Step 2 is not optional. "There seem to be two tags for time" is a hypothesis;
the counts tell you whether it is true and how much material moves.

### If you are an agent doing this

**Do:**

* Look at the counts before proposing a change, and put them in the issue or PR.
* Use `--dry-run` and read what it lists.
* Prefer an **alias** over a mass retag when two tags mean the same thing. It is
  one line, it is reversible, and it does not touch a single note.
* Export and show the diff. A tag change nobody can see is a tag change nobody
  agreed to.

**Do not:**

* Invent an axis. Adding one to `facets.yaml` changes how the whole course is
  read; propose it, do not do it.
* Retag material you have not looked at.
* Resolve an issue you did not act on.
* Touch a note `id`. That is rule 1, and it is not negotiable.
* Treat a taxonomy warning as a reason to quarantine material. A badly filed note
  is still a perfectly good note, and `validate` says so — these problems are
  never fatal.
