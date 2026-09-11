# Writing exercises

The **Create** tab is where material is written — the words, the answers, and how
an exercise is asked. It makes new sets and new exercises, and it opens any set
you already have to add to it or change it.

It is the only editor in the app. [Manage](managing.md) files material — names,
tags, which set something is in, what goes and what stays — and hands anything
that needs writing to this tab, on the exercise you were looking at.

```
┌ Create ─────────────────────────────────────────────────────────────────┐
│ set [ licao-2026-09-18 ▾ ]  licao-2026-09-18   [ Futuro simples ]      3 │
├──────────────────┬────────────────────────────┬─────────────────────────┤
│ 1  vou           │  gap ▾        makes 1 card │  asked as               │
│ 2  vai           │  prompt *                  │  [typein] [word bank]   │
│ 3  vamos      ⚠  │  Ela ___ trabalhar…        │  [multiple choice]      │
│                  │  answers *                 │  choice: needs 2 wrong  │
│  + add ⧉ dup     │  vai                       │  answers, has 0         │
│  ▤ paste several │  ▸ more: cue · hint · …    │                         │
│                  │                            │  as the learner sees it │
│                  │                            │  ┌───────────────────┐  │
│                  │                            │  │ Ela ___ trabalhar │  │
│                  │                            │  │ [        ] Check  │  │
│                  │                            │  └───────────────────┘  │
├──────────────────┴────────────────────────────┴─────────────────────────┤
│ vamos: 'hint' contains the answer 'vamos'. Move the solved form into a   │
│        field shown after answering (explain)…                           │
│ [ Save ]   3 new → licao-2026-09-18                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

## The set

Pick one from the list to open it, or leave it on **New set…** and type a name.
The name becomes a directory when the course is exported, so it is written like
one: `licao-2026-09-18`, `gram-futuro`. Beside it is a readable title, which is
free text and changes nothing else.

**Rename…** changes what an existing set is called, or its id, and **every
exercise in it follows in one transaction**. Renaming a set is safe in a way
renaming an exercise is not: nothing in your progress is stored against a set.

If you have unsaved work and try to leave — another set, another tab — you are
asked first: **Save them first**, **Discard them**, or **Stay here**. There is
also a **Discard changes** button beside Save, for when you simply want the set
back the way it was.

## An exercise

Choose the **type** and fill in what it asks for. The required fields are shown;
everything else — hints, an explanation, audio, a name of your own — is behind
**more**.

Every field says **when it is seen**: *shown with the question* or *shown after
answering*. That distinction is the one that matters here, and the preview is
where you watch it happen — put the answer in the hint and it appears in the
question box, with a line underneath telling you the exercise would not be
served. See [How it works](../how-it-works.md#3-an-exercise-may-never-contain-its-own-answer).

**Paste several** takes one exercise per line, the fields separated by `|`, in
the order printed above the box. It is the quickest way in for a list of words.

## How it is asked

**asked as** decides the form: typed, word bank, multiple choice, flashcard.
Normally the exercise's type decides this for all its exercises at once; here one
exercise can say otherwise — a sentence you want built from a word bank rather
than typed, a word you only ever want to recognise.

A form that is not available is shown anyway, with the reason:

- *needs an answer of two words or more* — a one-word word bank is the answer
  with extra steps;
- *needs 2 wrong answers to choose between, has 0* — a multiple choice with
  nothing to eliminate is a coin toss, and a coin toss reads as knowledge to the
  scheduler. Type them into **distractors** under *more*, or wait: wrong answers
  are also mined from the rest of the course once the set is saved;
- *the self marking this exercise uses cannot judge a typein* — a flashcard is
  judged by you, and only the exercise types that ask for a self-rating can use
  it.

The preview is rendered by the same code the Study tab uses. It is the exercise,
not a picture of one — though nothing you do in it is graded.

## Save

**Save** writes the set: new exercises are created, ones you changed are updated,
ones you removed are archived. There is no Confirm step — writing an exercise
replaces nothing, so the button is the confirmation. The footer says what
happened.

Two things it may tell you:

- **⚠ … gives away its own answer and will not be served.** The exercise was
  saved, and it is quarantined until you fix it. Nothing is lost; nothing reaches
  a learner either.
- **… staged edit(s) superseded.** Something you had staged in
  [Manage](managing.md) described a version of an exercise you have just
  rewritten, so it was dropped rather than left to overwrite your work at the
  next Confirm.

## What happens to it afterwards

An exercise written here lives in the database, exactly like one that came from a
course file, and it is **not** removed by the next import — see
[ADR-0010](../architecture/decisions/0010-material-can-be-written-in-the-app.md).

`repetita export <course> --to courses/<id>` writes the whole course back out as
ordinary YAML, which is how a set written here becomes a file somebody can read,
review and put in a pull request:

```yaml
notetype: gap
notes:
- id: licao-2026-09-18.vou
  prompt: Amanhã eu ___ estudar.
  answers:
  - vou
```

**An id can never change.** It is made from the answer when you first save, it
is not shown in this tab, and from then on it is the key your progress on that
exercise is stored under. Renaming one would delete that progress with nothing on
screen to show for it.
