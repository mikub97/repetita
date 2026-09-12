# repetita

An open-source engine for learning a language from scratch, built around one
idea: **you should practise the thing you are worst at, at the moment you are
about to forget it.**

It is a spaced-repetition system, but not a flashcard app. A flashcard has one
side and one answer. Here, a piece of material can be asked several ways — recognise
it, produce it, hear it, correct it — and each of those is scheduled on its own,
because knowing what *saudade* means and being able to say it are not the same
knowledge and do not decay at the same rate.

## The three tabs

<div class="grid cards" markdown>

- **[Study](using/studying.md)**

    One question at a time, chosen for you. What is due, what is new, in the
    order that keeps both moving.

- **[Design](using/designing.md)**

    A plan: drag the topics you care about into a priority list, see what
    tomorrow would look like, then practise it.

- **[Manage](using/managing.md)**

    Everything the database holds, and full editing of it — fix a typo, move an
    exercise, remove a set, add new material.

</div>

## What makes it different

**Your history is sacred.** Course material is owned by the database and can be
edited, imported and archived. Your schedule and your answers are never rebuilt
from anything, ever. Material that leaves a course is archived, not deleted, so
the history behind it stays reachable ([ADR-0006](architecture/decisions/0006-the-database-owns-the-material.md)).

**The engine knows no Portuguese.** Everything language-specific is course
configuration — the note types, the tags, the grading rules. The course that
comes with it teaches Brazilian Portuguese to Polish speakers; nothing in the
code knows that.

**An exercise may never contain its own answer.** The system this replaced had a
hint reading `fim de semana = weekend` for the answer *fim de semana*. Material
that gives itself away is refused, not warned about, and answers do not reach
the browser while a question is open.

**Decisions are written down with the measurement that decided them.** The
[Decisions](architecture/decisions/README.md) section is the honest history of
this project, including the ideas that were tried and lost.

## Getting it running

**[Installing it](install.md)** is a page of its own, because "clone the repo"
is not an answer for everybody. It covers Windows and macOS, needs no
administrator rights, and assumes you have never opened a terminal.

If you have, and you want the short version:

```bash
git clone https://github.com/mikub97/repetita
cd repetita
uv sync --all-extras
repetita import courses/pt-br-from-pl --yes
repetita serve pt-br-from-pl --open
```

The sample course is small, CC BY-SA, and enough to see all four tabs work.
Then read **[your first half hour](tutorial.md)**.

Bringing your own material? Start with [the ways in](adding-material.md).
