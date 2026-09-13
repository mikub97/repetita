"""
Asking questions about the material.

One query, and one selector language behind it. "The A2 grammar cards about
prepositions that are still new" is `level=A2,track=gramatica,topic=preposicoes,
state=new` -- the same string whether it arrives from the CLI, the web API, or a
row in `plan_priorities`. Three parsers for one idea is how they drift.

Dimensions are of two kinds and the caller does not need to know which:

* **built-in** -- `state`, `unit`, `notetype`, `template`, `course`; columns that
  are already on a row.
* **facet axes** -- whatever `facets.yaml` declares (`level`, `track`, `topic`,
  `source`); a join through `note_facets`.

`state` reads the denormalised `card_state.bucket` rather than recomputing a
threshold here. There is one definition of what "mature" means, in
`core.buckets`, and this must not become a second one.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from ..core.buckets import NEW
from ..core.mastery import Mastery, tally
from .users import DEFAULT_USER

#: Dimensions that are columns rather than facet values.
BUILTIN: dict[str, str] = {
    "state": f"COALESCE(s.bucket, '{NEW}')",
    "unit": "n.unit",
    "notetype": "c.notetype",
    "template": "c.template",
    "course": "n.course",
}

#: Axis and value names come from user input and are interpolated into SQL as
#: table aliases, which cannot be parameterised. Everything else binds.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")


class SelectorError(ValueError):
    """A selector that cannot be honoured, phrased for whoever typed it."""


@dataclass(frozen=True, slots=True)
class Row:
    """One group: what it is, and how much material is in it."""

    keys: dict[str, str]
    cards: int
    notes: int


def parse_selector(text: str | None) -> dict[str, list[str]]:
    """
    `topic=comida,state=new` -> `{"topic": ["comida"], "state": ["new"]}`.

    Repeating a dimension widens rather than contradicts:
    `state=new,state=learning` means either, which is what a person clicking two
    checkboxes means. Contradiction would make an empty result look like an empty
    course.
    """
    out: dict[str, list[str]] = {}
    if not text:
        return out
    for clause in (c.strip() for c in text.split(",") if c.strip()):
        name, sep, value = clause.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not value:
            raise SelectorError(f"{clause!r} is not name=value")
        if not _NAME.match(name):
            raise SelectorError(f"{name!r} is not a usable dimension name")
        out.setdefault(name, []).append(value)
    return out


def _axis_alias(axis: str) -> str:
    if not _NAME.match(axis):
        raise SelectorError(f"{axis!r} is not a usable dimension name")
    return f"f_{axis}"


@dataclass(frozen=True, slots=True)
class _Query:
    sql: str
    params: list[object] = field(default_factory=list)


#: What a free-text search looks in. `fields` is JSON in a TEXT column, so this
#: matches an answer, a prompt and an explanation alike -- which is what somebody
#: typing a word they half-remember means. `tags` is JSON too, and `label` is the
#: name the boards show.
#:
#: Deliberately not a `name=value` clause in the selector: that grammar says
#: "this dimension has this value", and "some text appears somewhere in this
#: note" is a different kind of question. Bending one into the other would have
#: cost the selector its meaning.
_SEARCH = "(lower(n.fields) LIKE ? OR lower(n.tags) LIKE ? OR lower(COALESCE(n.label,'')) LIKE ?)"


def _build(
    group_by: list[str], where: dict[str, list[str]], user_id: int, text: str | None = None
) -> _Query:
    joins: list[str] = []
    conditions: list[str] = []
    params: list[object] = [user_id]
    selects: list[str] = []

    facets = {d for d in [*group_by, *where] if d not in BUILTIN}
    for axis in sorted(facets):
        alias = _axis_alias(axis)
        joins.append(f"JOIN note_facets {alias} ON {alias}.note_id = n.id AND {alias}.axis = ?")
        params.append(axis)

    for dim in group_by:
        expr = BUILTIN.get(dim, f"{_axis_alias(dim)}.value")
        selects.append(f"{expr} AS d_{len(selects)}")

    for dim, values in where.items():
        expr = BUILTIN.get(dim, f"{_axis_alias(dim)}.value")
        marks = ",".join("?" for _ in values)
        conditions.append(f"{expr} IN ({marks})")
        params.extend(values)

    if text and text.strip():
        conditions.append(_SEARCH)
        like = f"%{text.strip().lower()}%"
        params.extend([like, like, like])

    # The facet joins land after the `?` for user_id, so they must be ordered
    # into `params` the same way they appear in the SQL. They are: user_id binds
    # in the LEFT JOIN, which comes first.
    sql = (
        f"SELECT {', '.join(selects) + ', ' if selects else ''}"
        "COUNT(DISTINCT c.id) AS cards, COUNT(DISTINCT n.id) AS notes "
        "FROM cards c "
        "JOIN notes n ON n.id = c.note_id AND n.archived_at IS NULL "
        "LEFT JOIN card_state s ON s.card_id = c.id AND s.user_id = ? "
        + " ".join(joins)
        + " WHERE c.archived_at IS NULL AND c.scheduled = 1"
        + ("".join(f" AND {c}" for c in conditions))
        + (
            f" GROUP BY {', '.join(str(i + 1) for i in range(len(selects)))} "
            f"ORDER BY {', '.join(str(i + 1) for i in range(len(selects)))}"
            if selects
            else ""
        )
    )
    return _Query(sql, params)


def catalogue(
    con: sqlite3.Connection,
    *,
    group_by: list[str] | None = None,
    where: dict[str, list[str]] | None = None,
    user_id: int = DEFAULT_USER,
    text: str | None = None,
) -> list[Row]:
    """
    Count material, grouped however you ask.

    `text` narrows to notes containing it -- in any field, in a tag, or in the
    name. Still counts: a group that matches is reported with a number, never
    with the note that matched, which is what keeps this from becoming a way to
    read the course.

    Only scheduled, unarchived cards are counted, because this answers "what is
    there to study" -- material that has left the course is not an answer to
    that, and including it would make every total disagree with the queue.
    """
    dims = list(group_by or [])
    filters = dict(where or {})
    query = _build(dims, filters, user_id, text)
    rows = con.execute(query.sql, query.params).fetchall()
    return [
        Row(
            keys={d: r[f"d_{i}"] for i, d in enumerate(dims)},
            cards=int(r["cards"]),
            notes=int(r["notes"]),
        )
        for r in rows
    ]


def card_ids_for(
    con: sqlite3.Connection,
    where: dict[str, list[str]] | None = None,
    *,
    user_id: int = DEFAULT_USER,
) -> list[str]:
    """
    The cards a selector picks out, in content order.

    The same key as `policies/daily.py:scheduled_cards`, and it has to stay the
    same key: two definitions of "content order" in one codebase is how a
    preview stops agreeing with the session it is previewing.
    """
    filters = dict(where or {})
    query = _build([], filters, user_id)
    sql = (
        query.sql.replace(
            "COUNT(DISTINCT c.id) AS cards, COUNT(DISTINCT n.id) AS notes",
            "DISTINCT c.id AS id, n.unit AS unit, n.ord AS ord, "
            "COALESCE(u.ord, 999999) AS unit_ord",
            1,
        ).replace(
            "JOIN notes n ON n.id = c.note_id AND n.archived_at IS NULL ",
            "JOIN notes n ON n.id = c.note_id AND n.archived_at IS NULL "
            "LEFT JOIN units u ON u.course = n.course AND u.id = n.unit ",
            1,
        )
        + " ORDER BY unit_ord, n.unit, n.ord, c.id"
    )
    return [r["id"] for r in con.execute(sql, query.params)]


def note_ids_for(
    con: sqlite3.Connection,
    where: dict[str, list[str]] | None = None,
    *,
    user_id: int = DEFAULT_USER,
) -> list[str]:
    """The notes a selector picks out. What `repetita tag` operates on."""
    filters = dict(where or {})
    query = _build([], filters, user_id)
    sql = query.sql.replace(
        "COUNT(DISTINCT c.id) AS cards, COUNT(DISTINCT n.id) AS notes",
        "DISTINCT n.id AS id",
        1,
    )
    return sorted(r["id"] for r in con.execute(sql, query.params))


def mastery_by(
    con: sqlite3.Connection,
    dimension: str,
    *,
    where: dict[str, list[str]] | None = None,
    user_id: int = DEFAULT_USER,
) -> dict[str, Mastery]:
    """
    How well each value of a dimension is known.

    Two counts per group rather than one, because `bucket_of` folds every
    retirement into `retired` and the difference between "I know this" and a
    card that earned its way out is not recoverable from the bucket. It lives in
    `card_state.retired_reason`, so it is read alongside.
    """
    rows = catalogue(con, group_by=[dimension, "state"], where=where, user_id=user_id)
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        buckets.setdefault(row.keys[dimension], {})[row.keys["state"]] = row.cards

    declared = {
        r[dimension]: int(r["n"]) for r in _declared_rows(con, dimension, where or {}, user_id)
    }
    return {
        value: tally(counts, declared=declared.get(value, 0)) for value, counts in buckets.items()
    }


def _declared_rows(
    con: sqlite3.Connection, dimension: str, where: dict[str, list[str]], user_id: int
) -> list[sqlite3.Row]:
    """The "I know this" count per group, kept apart from earned retirements."""
    query = _build([dimension], where, user_id)
    sql = query.sql.replace(
        "COUNT(DISTINCT c.id) AS cards, COUNT(DISTINCT n.id) AS notes",
        f"{BUILTIN.get(dimension, f'{_axis_alias(dimension)}.value')} AS {dimension}, "
        "COUNT(DISTINCT c.id) AS n",
        1,
    ).replace(
        " WHERE c.archived_at IS NULL",
        " WHERE s.retired_reason = 'declared' AND c.archived_at IS NULL",
        1,
    )
    # `_build` already selected the dimension as `d_0`; drop that duplicate.
    sql = sql.replace(
        f"SELECT {BUILTIN.get(dimension, f'{_axis_alias(dimension)}.value')} AS d_0, ", "SELECT ", 1
    )
    return con.execute(sql, query.params).fetchall()
