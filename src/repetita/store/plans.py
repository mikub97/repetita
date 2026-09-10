"""
What the learner wants studied, as opposed to what they have studied.

A plan is a priority list -- topics dragged into an order -- plus knobs. It is
progress-side data: never rebuilt from content, and never derived from anything.
It is the only record of *intent* in the database, and intent is not recoverable
from a review log.

Every change writes a **revision**, and every answer records which revision was
in force (`review_log.plan_revision_id`). That is ADR-0003's lesson applied
again: the predecessor kept aggregates and threw the sequence away, and it is
the one decision that could not be undone afterwards. "Did making it harder
actually help?" is exactly that shape of question, and it is unanswerable unless
the link is recorded from the first day rather than added once someone wants the
answer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

DEFAULT_USER = 1

#: Tuning a plan may override. Each needs a row in `docs/tuning.md` saying which
#: symptom it treats -- `policies/daily.py` states that rule and it is not
#: decoration.
KNOBS = (
    "new_every",
    "daily_target",
    "batch",
    "gate_threshold",
    "template_bias",
    "form_bias",
    "desired_retention",
    "consolidation",
)


@dataclass(frozen=True, slots=True)
class Priority:
    rank: int
    axis: str
    value: str
    #: `None` means "derive from rank", which is what dragging a row means.
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    id: int
    name: str
    course: str
    active: bool = False
    priorities: tuple[Priority, ...] = ()
    knobs: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> str:
        return json.dumps(
            {
                "priorities": [
                    {"rank": p.rank, "axis": p.axis, "value": p.value, "weight": p.weight}
                    for p in self.priorities
                ],
                "knobs": self.knobs,
            },
            sort_keys=True,
            ensure_ascii=False,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create(
    con: sqlite3.Connection,
    name: str,
    course: str,
    *,
    active: bool = False,
    user_id: int = DEFAULT_USER,
) -> Plan:
    stamp = _now()
    with con:
        if active:
            con.execute("UPDATE study_plans SET active = 0 WHERE user_id = ?", (user_id,))
        cur = con.execute(
            "INSERT INTO study_plans(user_id,name,course,active,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, name, course, int(active), stamp, stamp),
        )
    plan = Plan(int(cur.lastrowid or 0), name, course, active)
    _record_revision(con, plan)
    return plan


def _record_revision(con: sqlite3.Connection, plan: Plan) -> int:
    with con:
        cur = con.execute(
            "INSERT INTO plan_revisions(plan_id, changed_at, snapshot) VALUES(?,?,?)",
            (plan.id, _now(), plan.snapshot()),
        )
    return int(cur.lastrowid or 0)


def set_priorities(con: sqlite3.Connection, plan_id: int, priorities: list[Priority]) -> Plan:
    """
    Replace the whole list.

    Wholesale rather than per-row because the list *is* the ordering: applying
    two independent edits to it would produce an order neither person chose.
    """
    with con:
        con.execute("DELETE FROM plan_priorities WHERE plan_id = ?", (plan_id,))
        con.executemany(
            "INSERT INTO plan_priorities(plan_id,rank,axis,value,weight) VALUES(?,?,?,?,?)",
            [(plan_id, i, p.axis, p.value, p.weight) for i, p in enumerate(priorities)],
        )
        con.execute("UPDATE study_plans SET updated_at = ? WHERE id = ?", (_now(), plan_id))
    plan = get(con, plan_id)
    assert plan is not None
    _record_revision(con, plan)
    return plan


def set_knobs(con: sqlite3.Connection, plan_id: int, knobs: dict[str, object]) -> Plan:
    unknown = sorted(set(knobs) - set(KNOBS))
    if unknown:
        raise ValueError(f"unknown knob(s): {', '.join(unknown)}")
    with con:
        con.executemany(
            "INSERT INTO plan_knobs(plan_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(plan_id,key) DO UPDATE SET value=excluded.value",
            [(plan_id, k, json.dumps(v)) for k, v in knobs.items()],
        )
        con.execute("UPDATE study_plans SET updated_at = ? WHERE id = ?", (_now(), plan_id))
    plan = get(con, plan_id)
    assert plan is not None
    _record_revision(con, plan)
    return plan


def get(con: sqlite3.Connection, plan_id: int) -> Plan | None:
    row = con.execute("SELECT * FROM study_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    priorities = tuple(
        Priority(int(r["rank"]), r["axis"], r["value"], r["weight"])
        for r in con.execute(
            "SELECT * FROM plan_priorities WHERE plan_id = ? ORDER BY rank", (plan_id,)
        )
    )
    knobs = {
        r["key"]: json.loads(r["value"])
        for r in con.execute("SELECT key, value FROM plan_knobs WHERE plan_id = ?", (plan_id,))
    }
    return Plan(int(row["id"]), row["name"], row["course"], bool(row["active"]), priorities, knobs)


def active(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> Plan | None:
    row = con.execute(
        "SELECT id FROM study_plans WHERE user_id = ? AND active = 1 LIMIT 1", (user_id,)
    ).fetchone()
    return get(con, int(row["id"])) if row else None


def activate(con: sqlite3.Connection, plan_id: int, *, user_id: int = DEFAULT_USER) -> Plan | None:
    with con:
        con.execute("UPDATE study_plans SET active = 0 WHERE user_id = ?", (user_id,))
        con.execute(
            "UPDATE study_plans SET active = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (_now(), plan_id, user_id),
        )
    return get(con, plan_id)


def all_plans(con: sqlite3.Connection, *, user_id: int = DEFAULT_USER) -> list[Plan]:
    return [
        p
        for r in con.execute("SELECT id FROM study_plans WHERE user_id = ? ORDER BY id", (user_id,))
        if (p := get(con, int(r["id"]))) is not None
    ]


def latest_revision(con: sqlite3.Connection, plan_id: int) -> int | None:
    """The revision an answer given right now should be filed under."""
    row = con.execute(
        "SELECT id FROM plan_revisions WHERE plan_id = ? ORDER BY id DESC LIMIT 1", (plan_id,)
    ).fetchone()
    return int(row["id"]) if row else None


def delete(con: sqlite3.Connection, plan_id: int) -> None:
    """
    Remove a plan and its current shape.

    `plan_revisions` is left behind on purpose: answers in `review_log` point at
    those rows, and deleting them would turn a recorded fact -- this answer was
    given under that plan -- into a dangling id nobody can resolve.
    """
    with con:
        con.execute("DELETE FROM plan_priorities WHERE plan_id = ?", (plan_id,))
        con.execute("DELETE FROM plan_knobs WHERE plan_id = ?", (plan_id,))
        con.execute("DELETE FROM study_plans WHERE id = ?", (plan_id,))
