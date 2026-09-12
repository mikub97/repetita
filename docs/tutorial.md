# Your first half hour

This walks through one real session: answering, getting something wrong,
flagging a bad exercise, adding a word of your own, and filing it. It assumes
repetita is [installed](install.md) and open in a browser.

## The four tabs, and the flag

Across the top: a **flag**, the name, four tabs, and two counters.

```
 🇮🇹 ▾   repetita     Study   Design   Create   Manage      3 owed   7 / 30 today
```

The flag is the course you are studying. Click it to switch — each course keeps
its own schedule, its own counters and its own material, and the app opens where
you left off.

**owed** is what is due right now. **7 / 30 today** is how many you have answered
against the day's target. Neither is the size of the batch in front of you: the
queue arrives forty at a time so that a backlog of four hundred does not look
like a wall.

## Study: answer, and read the diff

A card shows you one side and asks for the other.

<div class="demo" data-card='{"id":"demo-1","form":"typein","fields":{"l1":"dziewczyna"},"ask":["l1"],"expect":"l2","notetype":"vocab","template":"produce"}'></div>

Type the answer and press <kbd>Enter</kbd>. Press <kbd>Enter</kbd> again for the
next one — the whole session works without the mouse.

When you are close but not exact, repetita shows you *where* you were wrong
rather than only that you were:

```
   you wrote      ragaza
   the answer     raga_z_za
```

Then you say how it felt, and that is what sets the next interval:

| | |
| --- | --- |
| **Again** | did not know it — it comes back today |
| **Hard** | got there, slowly — a shorter gap than last time, but still forward |
| **Good** | knew it |
| **Easy** | knew it instantly — a longer gap |

**Hard is a pass.** It moves the card forward, keeps your streak, and records no
lapse ([ADR-0002](architecture/decisions/0002-hard-is-a-pass.md)). Use it
honestly; the schedule is only as good as what you tell it.

### Two buttons under every card

**I know this already** takes a card out without making you answer it — for the
words you knew before you started. It is reversible.

**Something's wrong** is for when the *exercise* is broken rather than your
memory: the answer is wrong, the hint gives it away, the question is ambiguous.
Say which, and the card is suspended immediately so it stops costing you reviews
while it waits to be fixed.

That distinction is the point: answering is evidence about you, flagging is a
claim about the material.

## Manage: what you have, and where it lives

The **Manage** tab is every exercise you own, grouped into sets.

```
 Search…        no grouping ▾     196 exercises in 16 sets     + New set    Archived

 ┌─ Powitania ──── 16 ─┐  ┌─ Rodzina ─────── 12 ─┐   ┌────────────────────┐
 │ ○ cześć             │  │ ○ córka              │   │  córka        Close│
 │ ○ dzień dobry       │  │ ○ syn                │   │  vocab · it-02-… │
 │ ○ dziękuję          │  │ ○ brat               │   │  l2   figlia       │
 │ ○ proszę            │  │ ○ siostra            │   │  l1   córka        │
 └─────────────────────┘  └──────────────────────┘   │  tags A1, vocab    │
```

Click an exercise and the inspector opens on the right. It follows you down the
page, so it is still there when you are looking at a set near the bottom.

**Nothing here happens until you press Confirm.** Move an exercise to another
set, retag it, remove it — it is *staged*, and the drawer at the top says
exactly what will happen. Removing means **archived**, never deleted: the
material leaves the course and every answer you ever gave it stays.

## Create: write an exercise

The **Create** tab is where you write new material. Pick a set, choose a type,
fill the fields. The important part is on the right of each field:

```
  l2   figlia                       shown after answering
  l1   córka                        shown with the question
```

A field is either part of the **question** or part of the **answer**, and
repetita will refuse to serve an exercise whose question contains its own
answer — a hint reading `fim de semana = weekend` for the answer
`fim de semana` teaches nothing while looking perfectly normal. That refusal is
not a warning you can ignore; the exercise is quarantined until it is fixed.

Save applies immediately — Create is the one place that does not stage.

## Sharing what you wrote

Manage → **Import / export** → *Download the course* gives you the whole course
as one zip: ordinary files, the same ones a pull request contains. An exercise
you write in the app exists in no file until you do this.

Going the other way, *Import* reads a zip back. It shows you what it would do
before it does anything, names every exercise it would archive, and where you
have edited something that also changed in the file, **your version is kept**
unless you say otherwise.

## What next

* [Studying](using/studying.md) — the session screen in detail
* [Managing your material](using/managing.md) — filing, staging, the archive
* [Writing exercises](using/creating.md) — the Create tab in detail
* [Designing your lessons](using/designing.md) — deciding what comes next
* [Telling me what you think](feedback.md) — if you are testing this for me

!!! note "The exercise above is a demo"

    It runs the app's own renderer, but there is no server behind this page —
    nothing is graded and nothing is recorded.
