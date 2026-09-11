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

## Editing

**Click a row** to open the inspector: every field, with each one labelled
*shown with the question* or *shown after answering* — the distinction that
decides whether a hint is a hint or a giveaway. Edit the fields, the tags, or the
name.

**Drag rows between columns** to move exercises to another set. Select several
with ⌘/Ctrl-click and drag them together; drag a family header to move all its
forms at once.

**Two things cannot be changed**: an exercise's `id` and its type. The id is the
key your progress is stored under, and renaming one would delete that progress
with nothing on screen to show for it.

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

**Add material** opens a box for a lesson as you actually wrote it down — see
[the four ways in](../adding-material.md) and [the inbox](../inbox.md).
