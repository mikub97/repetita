"""
Giving an exercise a different id, and taking its history with it.

Ids were frozen because a rename orphans history: scheduling state is keyed on
`card_id`, which is keyed on the note id, and a course file that quietly says
something else leaves months of review log pointing at nothing. The rule said
*never rename*, which is true of renaming by hand and says nothing about whether
the operation is possible.

It is possible; it just has to be done in one transaction across everything that
refers to the exercise. That is **nine tables and ten columns**, which is exactly
why nobody wants to do it by hand and exactly why it belongs in one function.

What it deliberately does not do is invent a policy. It refuses a target that
already exists -- *including an archived one*, because an archived note still
owns the history behind it and handing its id over would hand the history over
with it (ADR-0010) -- and otherwise it moves everything and says what moved.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

#: An id becomes a key in YAML and part of a card id. Nothing enforced a shape
#: before because ids were never written by a command; now that one writes them,
#: the shape is worth stating: no whitespace, no `#` (the card separator), no
#: path separators.
_BAD = re.compile(r"[\s#/\\]")


class CannotRename(ValueError):
    """Said out loud, because every refusal here has a specific reason."""


@dataclass(frozen=True, slots=True)
class Moved:
    """What a rename actually moved, for the sentence it prints afterwards."""

    old: str
    new: str
    cards: int
    answers: int
    states: int
    reports: int
    distractors: int

    @property
    def history(self) -> int:
        return self.answers + self.states


def rename(con: sqlite3.Connection, old: str, new: str) -> Moved:
    """Rename a note and every reference to it, or raise `CannotRename`."""
    new = new.strip()
    if not new:
        raise CannotRename("the new id cannot be empty")
    if _BAD.search(new):
        raise CannotRename(
            f"{new!r} cannot be an id: no spaces, and no '#', '/' or '\\\\' -- "
            "a card id is '<note>#<template>', and an id becomes part of a filename"
        )
    if old == new:
        raise CannotRename("that is already its id")

    there = con.execute("SELECT id FROM notes WHERE id = ?", (old,)).fetchone()
    if there is None:
        raise CannotRename(f"no exercise called {old!r}")

    # Archived ones count. An archived note still owns its history, so its id is
    # spoken for even though nothing serves it.
    clash = con.execute("SELECT archived_at FROM notes WHERE id = ?", (new,)).fetchone()
    if clash is not None:
        raise CannotRename(
            f"{new!r} is already taken by an archived exercise -- its history is "
            "still stored under that id"
            if clash["archived_at"]
            else f"{new!r} is already taken"
        )

    stamp = datetime.now(UTC).isoformat()
    # `<note>#<template>`, so every card gets a new id of its own.
    cards = {
        r["id"]: f"{new}#{r['template']}"
        for r in con.execute("SELECT id, template FROM cards WHERE note_id = ?", (old,))
    }

    answers = states = reports = distractors = 0
    with con:
        for was, becomes in cards.items():
            answers += con.execute(
                "UPDATE review_log SET card_id = ? WHERE card_id = ?", (becomes, was)
            ).rowcount
            states += con.execute(
                "UPDATE card_state SET card_id = ? WHERE card_id = ?", (becomes, was)
            ).rowcount
            distractors += con.execute(
                "UPDATE distractors SET card_id = ? WHERE card_id = ?", (becomes, was)
            ).rowcount
            reports += con.execute(
                "UPDATE card_reports SET card_id = ? WHERE card_id = ?", (becomes, was)
            ).rowcount
            con.execute("UPDATE cards SET id = ?, note_id = ? WHERE id = ?", (becomes, new, was))
            # Handles are a fresh mapping every time the app starts, so the old
            # ones are dropped rather than moved. Keeping them would leave a
            # token pointing at a card id that no longer exists.
            con.execute("DELETE FROM card_handles WHERE card_id = ?", (was,))

        con.execute("UPDATE card_reports SET note_id = ? WHERE note_id = ?", (new, old))
        con.execute("UPDATE note_facets SET note_id = ? WHERE note_id = ?", (new, old))
        con.execute("UPDATE pending_changes SET note_id = ? WHERE note_id = ?", (new, old))
        # `edited_at` and `origin` last, and both matter.
        #
        # `origin` is the file a note came from, and after a rename it no longer
        # corresponds to anything in any file -- the file still says the old id.
        # Clearing it is the accurate statement, and it is also what stops the
        # next import from archiving the exercise that was just renamed: `sync`
        # spares notes that were never in a file, and until an export catches the
        # file up, this is one of them. The import sets it again when the file
        # and the database agree.
        con.execute(
            "UPDATE notes SET id = ?, origin = '', edited_at = ?, updated_at = ? WHERE id = ?",
            (new, stamp, stamp, old),
        )

    return Moved(
        old=old,
        new=new,
        cards=len(cards),
        answers=answers,
        states=states,
        reports=reports,
        distractors=distractors,
    )
