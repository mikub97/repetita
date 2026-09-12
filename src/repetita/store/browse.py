"""
Reading and editing the database as tables, for the admin page.

Generic over the schema on purpose: 25 tables, and a screen per table would be
25 screens that drift. What is *not* generic is which tables may be written to,
because that is not a detail of presentation -- it is the difference between a
useful admin page and the way a month of study disappears at one in the morning.

**Three kinds of table, and every write path asks which kind it is.**

*Cannot be rebuilt.* `review_log` and `card_state` are the only things in this
system that no export brings back (CLAUDE.md rule 1), and `plan_revisions` is
the row an answer points at to say what plan it was given under -- deleting one
turns a recorded fact into a dangling id. Read-only, and the page says why.

*Owned by another surface.* A write to `notes`, `cards`, `units`,
`note_facets`, `distractors` or `card_handles` owes four things: set
`edited_at`, leave `content_hash` alone, re-expand the cards, `reclassify`.
`store/material.py` exists to owe them. A generic editor that mapped arbitrary
row edits onto those functions would be a half-correct mapping, and a
half-correct mapping is worse than a refusal that names where to go instead --
so these are read-only here and editable in Manage and Create, which already do
it properly.

*Its own.* Everything else: accounts, enrolments, plans, settings, drafts,
issues, aliases, facet declarations. A row there means what it says, and editing
it owes nothing to anybody.

The strictness is deliberate and is the answer to "shouldn't the admin page be
able to fix anything?" -- it can, by going to the screen that knows how.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

#: Tables whose rows cannot be rebuilt from anything. The first two are rule 1
#: itself; the third is what `plans.delete` already refuses to touch, for the
#: reason recorded there.
IRREPLACEABLE = ("review_log", "card_state", "plan_revisions")

#: Tables another surface owns, and which view owns them. A view key rather than
#: a tab's name for the same reason the values below are codes.
ELSEWHERE = {
    "notes": "manage",
    "cards": "manage",
    "units": "manage",
    "note_facets": "manage",
    "distractors": "manage",
    # Regenerated, and meaningless on their own: there is nowhere to go.
    "card_handles": "",
}

#: Why a table is read-only, as a code rather than a sentence.
#:
#: The sentence is the client's, because UI text is translated and the engine
#: ships no UI strings as Python literals (CLAUDE.md). Same rule `ApiError`
#: already follows, and for the same reason: a client that has to match on prose
#: breaks on a typo fix.
FROZEN_IRREPLACEABLE = "irreplaceable"
FROZEN_ELSEWHERE = "owned_elsewhere"

#: Columns this module never reads and never writes, whatever the table's mode.
#:
#: Both of these are credentials, and an admin page is the wrong place for a
#: credential even when the person reading it is entitled to everything else.
#: `password_hash` shown is a hash over somebody's shoulder; `password_hash`
#: *editable* is pasting a hash you know into another person's row and signing
#: in as them, which no amount of being an admin makes acceptable -- there is a
#: form for setting a password and it goes through `store.users`.
SECRET_COLUMNS = {"users": ("password_hash",)}

#: Rows hidden the same way, for key-value tables where the secret is a row
#: rather than a column. `meta.secret_key` signs session cookies: anybody who
#: reads it can forge one for any account, admin or not.
SECRET_ROWS = {"meta": ("key", ("secret_key",))}

#: What is shown in their place, so the column is visibly there and visibly not
#: for reading. Blanking it would look like an empty password.
REDACTED = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"

#: How many rows a page of the browser shows. Enough to see a pattern, few
#: enough that a table of 6367 cards does not arrive as one response.
PAGE = 50


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    rows: int
    #: Empty when the table is editable; otherwise a code saying why not.
    frozen: str = ""
    #: The view that owns it, when one does. Empty means nowhere.
    instead: str = ""
    #: Rows inside an otherwise editable table that are not: `(column, values)`.
    #: The page needs this to not draw a Delete button that always refuses --
    #: a control that cannot do anything invites the question of what it is for.
    locked: tuple[str, tuple[str, ...]] | None = None

    @property
    def editable(self) -> bool:
        return not self.frozen


@dataclass(frozen=True, slots=True)
class Page:
    table: Table
    rows: list[dict[str, Any]] = field(default_factory=list)
    offset: int = 0
    total: int = 0


def _names(con: sqlite3.Connection) -> list[str]:
    return [
        r["name"]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def describe(con: sqlite3.Connection, name: str) -> Table:
    """One table: its shape, its size, and whether it may be written to."""
    if name not in _names(con):
        raise LookupError(f"no table {name!r}")
    info = list(con.execute(f"PRAGMA table_info({name})"))
    count = con.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
    frozen = instead = ""
    if name in IRREPLACEABLE:
        frozen = FROZEN_IRREPLACEABLE
    elif name in ELSEWHERE:
        frozen = FROZEN_ELSEWHERE
        instead = ELSEWHERE[name]
    return Table(
        name=name,
        # Secret columns are not listed at all. A column the page can see the
        # name of but not the value invites the question of how to see it.
        columns=tuple(c["name"] for c in info if c["name"] not in SECRET_COLUMNS.get(name, ())),
        primary_key=tuple(c["name"] for c in info if c["pk"]),
        rows=int(count),
        frozen=frozen,
        instead=instead,
        locked=SECRET_ROWS.get(name),
    )


def tables(con: sqlite3.Connection) -> list[Table]:
    return [describe(con, n) for n in _names(con)]


def read(
    con: sqlite3.Connection,
    name: str,
    *,
    offset: int = 0,
    limit: int = PAGE,
    where: dict[str, str] | None = None,
) -> Page:
    """
    A page of rows, optionally narrowed by exact column matches.

    Exact matches rather than free text: a LIKE over every column of every table
    is a query nobody can predict the cost of, and the thing an admin page is
    actually for is "show me this user's rows", which is an equality.
    """
    table = describe(con, name)
    clause, params = _where(table, where)
    total = con.execute(f"SELECT COUNT(*) AS n FROM {name}{clause}", params).fetchone()["n"]
    order = ", ".join(table.primary_key) or table.columns[0]
    # Named columns rather than `*`: a secret column must not be selected, and
    # `*` would keep working the day somebody adds another one.
    picked = ", ".join(table.columns)
    rows = con.execute(
        f"SELECT {picked} FROM {name}{clause} ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, max(1, min(limit, 500)), max(0, offset)),
    )
    return Page(
        table=table,
        rows=[_redact(name, dict(r)) for r in rows],
        offset=offset,
        total=int(total),
    )


def _redact(name: str, row: dict[str, Any]) -> dict[str, Any]:
    """Blank the value of a secret row, keeping the row itself visible."""
    column, values = SECRET_ROWS.get(name, ("", ()))
    if column and str(row.get(column)) in values:
        return {k: (REDACTED if k != column else v) for k, v in row.items()}
    return row


def _where(table: Table, where: dict[str, str] | None) -> tuple[str, tuple[Any, ...]]:
    """
    Column names checked against the table rather than quoted.

    The only safe way: a column name cannot be a bound parameter, so the
    alternative is escaping identifiers by hand. Anything not a real column of
    this table is dropped rather than refused -- a stale filter in a URL should
    show the table, not an error page.
    """
    picked = {k: v for k, v in (where or {}).items() if k in table.columns}
    if not picked:
        return "", ()
    clause = " WHERE " + " AND ".join(f"{k} = ?" for k in picked)
    return clause, tuple(picked.values())


class Frozen(PermissionError):
    """A write to a table this module will not write to. Says why, and where to go."""


def _must_be_editable(table: Table) -> None:
    """
    Refuse a write, in the same shape as the rest of the refusals here.

    The message is a code and the table it is about, not a sentence: the client
    turns it into prose, and the prose it turns it into is the same prose it
    already shows in the banner over that table.
    """
    if table.editable:
        return
    raise Frozen(f"{table.frozen}:{table.name}")


def update(con: sqlite3.Connection, name: str, key: dict[str, Any], values: dict[str, Any]) -> int:
    """Change one row, addressed by its whole primary key."""
    table = describe(con, name)
    _must_be_editable(table)
    fields = {k: v for k, v in values.items() if k in table.columns and k not in table.primary_key}
    # `table.columns` already excludes the secret ones, so a column filtered out
    # there cannot be written here either -- said out loud because the two
    # protections looking like one is how the second gets removed as redundant.
    secret = SECRET_COLUMNS.get(name, ())
    assert not any(k in secret for k in fields), "a secret column reached the write path"
    column, keys = SECRET_ROWS.get(name, ("", ()))
    if column and str(key.get(column)) in keys:
        raise Frozen(f"{name}.{key[column]} is not editable here")
    if not fields:
        # A primary key is not editable in place: changing it is deleting a row
        # and writing another, and whatever pointed at the old one would not
        # follow. `repetita rename-id` exists for the one case where that is
        # wanted and it moves nine tables to do it.
        raise Frozen("nothing to change; a primary key is not edited in place")
    clause, params = _key(table, key)
    done = con.execute(
        f"UPDATE {name} SET {', '.join(f'{k} = ?' for k in fields)}{clause}",
        (*fields.values(), *params),
    )
    con.commit()
    return int(done.rowcount)


def delete(con: sqlite3.Connection, name: str, key: dict[str, Any]) -> int:
    """Remove one row, addressed by its whole primary key."""
    table = describe(con, name)
    _must_be_editable(table)
    column, keys = SECRET_ROWS.get(name, ("", ()))
    if column and str(key.get(column)) in keys:
        # Deleting it would sign everybody out and issue a new one on the next
        # start -- recoverable, but not something to do by clicking a row.
        raise Frozen(f"{name}.{key[column]} is not deletable here")
    clause, params = _key(table, key)
    done = con.execute(f"DELETE FROM {name}{clause}", params)
    con.commit()
    return int(done.rowcount)


def _key(table: Table, key: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """
    The whole primary key, or nothing.

    Partial keys are refused rather than filled in: `DELETE FROM card_state
    WHERE card_id = ?` without the `user_id` is one keystroke from deleting
    every account's row for that card, and the shape of this function is what
    makes that impossible rather than unlikely.
    """
    if not table.primary_key:
        raise Frozen(f"{table.name} has no primary key; this browser will not write to it")
    missing = [c for c in table.primary_key if c not in key]
    if missing:
        raise Frozen(f"name the whole primary key; missing: {', '.join(missing)}")
    return " WHERE " + " AND ".join(f"{c} = ?" for c in table.primary_key), tuple(
        key[c] for c in table.primary_key
    )
