# Managing your material

Everything the database holds, and full editing of it. This is the one screen
that shows answers — you cannot fix a typo in an answer you cannot see — and the
deliberate exception to the rule that governs everywhere else
([ADR-0008](../architecture/decisions/0008-the-management-surface-sees-everything.md)).

## The board

Sets as columns, exercises as rows.

```
 fala-capoeiristas  15     gram-atras-passado-ainda 10     gram-contracoes-eque 17
 ○ Bom dia a todos…        ○ passada                       ○ deste
 ○ Mestre, posso…          ○ atrás              03         ○ nessa
 ○ Desculpa, pode…         ○ atrás              05         ○ é que          07
 ○ Como se chama esse…     ○ ainda              06         ○ é que          08
```

Each row is the exercise's **[name](../labels.md)** — a short handle derived from
its answer. Hover for the whole question, or click to open it.

Where a name repeats in a column, the row shows what separates it (`03`, `07`).
Names are allowed to repeat: twenty exercises about grammatical gender all
answer `o` or `a`, and inventing unique text for them would be inventing text
nobody wrote.

A word in several forms collapses into one expandable row, so six forms of
*morar* take one line until you want the six.

## Filing

This tab is where material is **organised**. The words themselves are written on
the [Create](creating.md) tab — one editor, with the preview and the live checks,
rather than two that drift apart.

**Click a row** to open it. You get the exercise as a learner would meet it: every
field it has, in reading order, each labelled *shown with the question* or *shown
after answering* — the distinction that decides whether a hint is a hint or a
giveaway. The fields are there to be read; **Edit this exercise →** opens the
same exercise in Create, on the row you were looking at.

What you can change here is how it is filed:

- **name** — what the board, the drawer and your plans call it;
- **tags** — how it is classified, which is what the Design tab groups by;
- **set** — which set it belongs to. The same move a drag makes, for when the two
  sets are four rows apart.

**Drag rows between columns** to move exercises the quick way. Select several
with ⌘/Ctrl-click and drag them together; drag a family header to move all its
forms at once. While you drag, the set that would receive them is marked.

**Drag a set by its ⠿ handle** to rearrange the board. A caret shows the gap it
will land in. This changes nothing about the course — it is your view, remembered
in this browser, and **Reset layout** puts it back.

**Two things cannot be changed from the app**: an exercise's `id` and its type.
The id is the key your progress is stored under. It can be changed — from the
terminal, with `repetita rename-id`, which carries the history across to the new
id — but never by typing over it, which would leave the progress behind.

## What is waiting

Four kinds of thing can be outstanding at once, and only one of them used to be
visible anywhere: changes staged here, lesson notes queued for an agent,
exercises you flagged while studying, and problems you filed from the Design tab.

**Waiting**, in the header, shows all four with what happens next for each. The
count appears on every tab, so a change staged here and forgotten is no longer
invisible the moment you look at something else.

## Nothing happens until you press Confirm

Every edit is *staged*. It is recorded on the server — so a refresh, a second
tab or a closed laptop does not lose it — and a drawer at the top says what will
happen:

```
 3 changes not yet applied
   atrás          unit          passado-ainda  →  contracoes-eque
   Bom dia        fields        Bom dia a todo →  Bom dia a todos
   test           remove set    empty — nothing goes with it

   [ Confirm ]   Discard
```

**Confirm** applies all of them, in one transaction, or none of them. A
half-applied batch is the state nobody can reason about.

If an edit makes an exercise unservable — usually an answer showing in a field
that is visible while the question is open — it is still applied, and Confirm
says which, loudly. An exercise that silently stops appearing is the worst of
the available outcomes.

## Removing things

**Remove from the course** in the inspector, or **×** on a set's header. Both are
staged like everything else and land in the drawer first — removing a set says
how many exercises go with it before you agree.

Nothing is ever deleted. Removal means *archived*: the material leaves the
course, and every schedule and answer behind it stays reachable. A mistake is
undone by restoring rather than by writing it again.

## Adding material

**+ New set** makes an empty set here, before it exists in any course file.

**Capture a lesson** opens a box for a lesson you have not turned into
exercises yet — see
[the ways in](../adding-material.md) and [the inbox](../inbox.md).

## The course as a file

**Import / export** is the whole course in one zip, both directions.

**Download the course** writes everything here — including exercises you wrote
in the app, which until now existed in no file at all — as the same course
directory `repetita export` produces. That is what a pull request contains, and
what somebody else can fork.

**Import** reads one back, and shows you what it would do before it does
anything:

* what would be **added** and **updated**;
* every exercise that would be **archived**, by name. A zip that does not mention
  an exercise is how removing one is expressed, and this is the part that running
  the import again does not undo — so it is listed, never counted;
* every **conflict**: an exercise changed both here and in the zip. Yours is kept
  unless you say otherwise, one exercise at a time. Nothing picks a winner for
  you, because the loser would not be recoverable and nothing on screen would
  say so.

A snapshot of the database is taken before anything is written, and the toast
names it — `repetita restore <name>` puts everything back.

Importing a zip for a *different* course is refused rather than merged.
