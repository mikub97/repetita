"""
Turning a style into something a pure policy can read.

`store/styles.py` holds what the learner asked for, in the terms they asked it
in: "order by the level axis", "follow plan 3". `policies/ordering.py` cannot
read any of that -- it has no database and is not allowed one. This module is
the seam: it does the lookups once, and hands down plain mappings.

The invariant worth stating, because the whole change rests on it: `Recipe()`
with every default is the behaviour that predates styles entirely. Not
approximately -- identically, and `tests/policies/test_styles.py` asserts it by
building the same session both ways and comparing the lists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..store import plans as store_plans
from ..store import styles as store_styles
from ..store.styles import BadStyle, Style
from ..store.users import DEFAULT_USER


@dataclass(frozen=True, slots=True)
class Recipe:
    """A style with everything it referred to already looked up."""

    style: Style = store_styles.DEFAULT
    #: card_id -> rank along the chosen ordered axis.
    axis_rank: dict[str, int] = field(default_factory=dict)
    #: card_id -> the weight the plan gives it.
    weight: dict[str, float] = field(default_factory=dict)
    #: The templates a learner has ranked, most wanted first.
    templates: tuple[str, ...] = ()
    #: Injected rather than computed, so `ordering` stays free of a clock.
    seed: str = ""
    #: True when the style named a plan that is no longer there. Reported rather
    #: than silent: falling back without saying so leaves somebody wondering why
    #: their queue changed, with nothing anywhere able to tell them.
    plan_missing: bool = False


def membership_of(con: sqlite3.Connection, card_ids: list[str]) -> dict[str, set[tuple[str, str]]]:
    """
    Which (axis, value) pairs each card belongs to, facets and built-ins.

    Moved here from `planned` because both policies need it now and neither
    should import the other. `planned` re-exports it, so its own callers and
    tests are unaffected.
    """
    if not card_ids:
        return {}
    out: dict[str, set[tuple[str, str]]] = {c: set() for c in card_ids}
    rows = con.execute(
        "SELECT c.id AS card_id, f.axis AS axis, f.value AS value "
        "FROM cards c JOIN note_facets f ON f.note_id = c.note_id "
        "WHERE c.archived_at IS NULL"
    )
    for r in rows:
        if r["card_id"] in out:
            out[r["card_id"]].add((r["axis"], r["value"]))
    for r in con.execute(
        "SELECT c.id AS card_id, n.unit AS unit, c.notetype AS notetype, c.template AS template "
        "FROM cards c JOIN notes n ON n.id = c.note_id WHERE c.archived_at IS NULL"
    ):
        if r["card_id"] in out:
            out[r["card_id"]] |= {
                ("unit", r["unit"]),
                ("notetype", r["notetype"]),
                ("template", r["template"]),
            }
    return out


def ordered_axes(con: sqlite3.Connection, course: str) -> dict[str, dict[str, int]]:
    """
    The axes a course declares an order for, and the rank of each value.

    Only `ordered` axes, because only they have an answer: `topic` is a set of
    names with no sequence, and inventing one would make "easiest first" mean
    "alphabetically first" -- which is the exact bug this whole change exists to
    remove, reintroduced one level up.
    """
    out: dict[str, dict[str, int]] = {}
    for r in con.execute(
        "SELECT v.axis AS axis, v.value AS value, v.ord AS ord "
        "FROM facet_values v JOIN facet_axes a ON a.course = v.course AND a.axis = v.axis "
        "WHERE v.course = ? AND a.ordered = 1 ORDER BY v.axis, v.ord",
        (course,),
    ):
        out.setdefault(r["axis"], {})[r["value"]] = int(r["ord"])
    return out


def _templates(style: Style) -> tuple[str, ...]:
    raw = style.knobs.get("template_order")
    if not isinstance(raw, list):
        return ()
    return tuple(v for v in raw if isinstance(v, str) and v)


def recipe_for(
    con: sqlite3.Connection,
    style: Style,
    today: object,
    *,
    course: str,
    user_id: int = DEFAULT_USER,
) -> Recipe:
    """
    Look up everything `style` refers to, once.

    Raises `BadStyle` for an axis this course does not declare ordered -- a
    refusal rather than a quiet fallback, because "sort by level" silently
    becoming "sort by whatever" is a setting that lies about itself. A missing
    *plan* is different: it can be deleted between a page load and an answer, so
    that one falls back and says so.
    """
    if style == store_styles.DEFAULT:
        return Recipe()

    axis_rank: dict[str, int] = {}
    if style.introductions == "axis":
        axes = ordered_axes(con, course)
        ranks = axes.get(style.intro_axis)
        if ranks is None:
            raise BadStyle(
                f"{course} does not declare {style.intro_axis!r} as an ordered axis; "
                f"ordered here: {', '.join(sorted(axes)) or 'none'}"
            )
        for card_id, keys in membership_of(con, _all_cards(con, course)).items():
            for axis, value in keys:
                if axis == style.intro_axis and value in ranks:
                    axis_rank[card_id] = ranks[value]

    weight: dict[str, float] = {}
    missing = False
    if style.plan_id is not None:
        plan = store_plans.get(con, style.plan_id, user_id=user_id)
        if plan is None:
            missing = True
        else:
            from .planned import weights_from_ranks

            shares = weights_from_ranks(plan.priorities)
            for card_id, keys in membership_of(con, _all_cards(con, course)).items():
                hit = keys & set(shares)
                if hit:
                    weight[card_id] = max(shares[k] for k in hit)

    return Recipe(
        style=style,
        axis_rank=axis_rank,
        weight=weight,
        templates=_templates(style),
        seed=f"{user_id}:{course}:{today}",
        plan_missing=missing,
    )


def _all_cards(con: sqlite3.Connection, course: str) -> list[str]:
    return [
        r["id"]
        for r in con.execute(
            "SELECT c.id AS id FROM cards c JOIN notes n ON n.id = c.note_id "
            "WHERE n.course = ? AND c.archived_at IS NULL",
            (course,),
        )
    ]
