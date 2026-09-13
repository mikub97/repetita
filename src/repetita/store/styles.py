"""
How one person wants their own queue built, in one course. "Jak się uczę".

Progress-side data, like a study plan: never rebuilt from content, never derived
from anything, and the only record there is of a preference.

**Not a plan** (ADR-0017). A plan is an *additional* path through the material,
asked for per request, and ADR-0007 reverted the first version that let one take
over the Study tab. This is the opposite shape: it configures the one path
everybody already has, and it is never asked for -- it is simply how that person
studies until they say otherwise. The two share a vocabulary of knobs and
nothing else.

Absence is the default rather than a missing row to repair. `get` answers with
`DEFAULT` when there is no row, which is the same argument `users.studying`
makes: no choice adds no clause, so a database that predates this table behaves
exactly as it did, and a person who has never opened the screen is not carrying
an implicit configuration they never made.

Every change writes a revision and every answer records which one was in force.
That is ADR-0003 applied a third time, and it is not optional here for the reason
it was not optional for `review_log`: a column added in six months leaves every
answer before it unattributable, and this is the path on which nearly every
answer is given.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from .plans import KNOBS
from .users import DEFAULT_USER


class BadStyle(ValueError):
    """A style somebody asked for that this course or this engine cannot honour."""


@dataclass(frozen=True, slots=True)
class Style:
    #: The named mode this came from, or `wlasny` once a knob has been touched.
    #: Carried rather than derived so the screen can say "Nadrabianie" instead of
    #: reverse-engineering a name out of five settings, and so a mode whose
    #: recipe changes later does not silently rename what somebody chose.
    mode: str = "kurs"
    introductions: str = "lesson"
    #: Which ordered facet axis `introductions="axis"` follows. Empty is the
    #: course's own answer -- see `context.recipe_for`, which refuses an axis the
    #: course has not declared `ordered`.
    intro_axis: str = ""
    debt: str = "overdue"
    #: "Skupienie": a selector narrowing which owed cards are served. Empty is
    #: the whole debt. ADR-0018, which supersedes ADR-0007 on this one point.
    focus: str = ""
    #: ISO date the focus lapses on. `None` is "until I say", and the client only
    #: offers that behind a confirm -- an expiry is what makes this bounded
    #: rather than merely visible.
    focus_until: str | None = None
    plan_id: int | None = None
    knobs: dict[str, object] = field(default_factory=dict)

    def active_focus(self, today: date) -> str:
        """
        The focus, or "" once `focus_until` has passed.

        Lapsing is a *read*, deliberately: a read that wrote would file the lapse
        as an edit at whatever moment the page happened to load, and would put a
        revision in the log that the learner did not make. The row keeps what it
        says; the builder stops honouring it; the screen offers to renew it.
        """
        if not self.focus:
            return ""
        if self.focus_until is None:
            return self.focus
        try:
            return self.focus if date.fromisoformat(self.focus_until) >= today else ""
        except ValueError:
            # An unparseable date is not a licence to hide the debt forever.
            return ""

    def snapshot(self) -> str:
        """What a revision records. Sorted, so two equal styles compare equal."""
        return json.dumps(
            {
                "mode": self.mode,
                "introductions": self.introductions,
                "intro_axis": self.intro_axis,
                "debt": self.debt,
                "focus": self.focus,
                "focus_until": self.focus_until,
                "plan_id": self.plan_id,
                "knobs": self.knobs,
            },
            sort_keys=True,
            ensure_ascii=False,
        )


#: What the engine does when nobody has said anything. Byte for byte the
#: behaviour that predates this module -- `tests/policies/test_styles.py` asserts
#: it against a session built with no style at all, rather than assuming it.
DEFAULT = Style()

#: The named modes, and what each one stands for.
#:
#: Named rather than numbered, which is the house argument (`settings.js` on
#: themes, `courses.js` on courses): "gate_threshold: 0.6" tells you the name of
#: a variable and nothing about what moving it does. The sentences a learner
#: reads live in the client -- `static/modes.js` -- because they are UI text;
#: what a mode *means* lives here, because it is behaviour.
MODES: dict[str, Style] = {
    # The course leads. Today's behaviour, under a name.
    "kurs": Style(mode="kurs"),
    # Clearing a backlog: the debt first and hardest, new material barely
    # trickling. Deliberately does not raise the gate -- a backlog is already
    # evidence of overload, and opening the tap wider is how it got there.
    "nadrabianie": Style(
        mode="nadrabianie",
        debt="weakest",
        knobs={"new_every": 8, "batch": 60},
    ),
    # A gentle day: a short session, and the ladder teaching for longer.
    "spokojny": Style(
        mode="spokojny",
        knobs={"batch": 20, "daily_target": 15, "ladder_steps": 2},
    ),
    # Working through the course in the order it was written, with no exemption
    # for whatever lesson happens to be recent.
    "sciezka": Style(mode="sciezka", introductions="course"),
    # Easiest material first, wherever it lives, by the course's own CEFR axis.
    "od-latwych": Style(mode="od-latwych", introductions="axis", intro_axis="level"),
    # Hard: bigger sessions, a stricter gate, and examined from first contact.
    "intensywnie": Style(
        mode="intensywnie",
        knobs={"batch": 60, "new_every": 2, "gate_threshold": 0.85, "ladder_steps": 0},
    ),
}

#: What `mode` becomes when a knob is turned by hand. The name must never claim
#: more than it can deliver: an edited preset is not the preset any more, which
#: is exactly how an edited theme behaves.
CUSTOM = "wlasny"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate(style: Style) -> Style:
    """
    Everything checkable without a database.

    The orderings are checked here; whether an *axis* exists and is ordered needs
    the course, so `policies/context.recipe_for` does that one. Splitting them is
    not tidiness: this function has to work in a test with no course loaded.
    """
    from ..policies.ordering import DEBT_ORDERINGS, ORDERINGS

    if style.introductions not in ORDERINGS:
        raise BadStyle(f"unknown ordering {style.introductions!r}")
    if style.debt not in DEBT_ORDERINGS:
        raise BadStyle(f"unknown debt ordering {style.debt!r}")
    unknown = sorted(set(style.knobs) - set(KNOBS))
    if unknown:
        raise BadStyle(f"unknown knob(s): {', '.join(unknown)}")
    if style.introductions == "axis" and not style.intro_axis:
        raise BadStyle("ordering by an axis needs an axis")
    if style.introductions == "plan" and style.plan_id is None:
        raise BadStyle("ordering by a plan needs a plan")
    if style.focus:
        from .catalogue import SelectorError, parse_selector

        try:
            parse_selector(style.focus)
        except SelectorError as bad:
            raise BadStyle(str(bad)) from None
    if style.focus_until is not None:
        try:
            date.fromisoformat(style.focus_until)
        except ValueError:
            raise BadStyle(f"{style.focus_until!r} is not a date") from None
    return style


def get(con: sqlite3.Connection, course: str, *, user_id: int = DEFAULT_USER) -> Style:
    """
    How this person studies this course. `DEFAULT` when they have not said.

    Never `None`: "has not chosen" and "has chosen the default" produce the same
    session, and making the caller tell them apart would put a branch in every
    call site to answer a question none of them has.
    """
    row = con.execute(
        "SELECT * FROM study_styles WHERE user_id = ? AND course = ?", (user_id, course)
    ).fetchone()
    if row is None:
        return DEFAULT
    try:
        knobs = json.loads(row["knobs"])
    except (TypeError, ValueError):
        knobs = {}
    return Style(
        mode=row["mode"],
        introductions=row["introductions"],
        intro_axis=row["intro_axis"],
        debt=row["debt"],
        focus=row["focus"],
        focus_until=row["focus_until"],
        plan_id=row["plan_id"],
        knobs=knobs if isinstance(knobs, dict) else {},
    )


def save(
    con: sqlite3.Connection,
    course: str,
    style: Style,
    *,
    user_id: int = DEFAULT_USER,
) -> Style:
    """
    Write it, and record what it was.

    The revision is written on every save, including a save that changes nothing:
    a revision is a record of when a setting was in force, not a diff, and
    skipping the no-op ones would leave a gap in exactly the sequence ADR-0003
    exists to keep.
    """
    style = _validate(style)
    with con:
        con.execute(
            "INSERT INTO study_styles"
            "(user_id,course,mode,introductions,intro_axis,debt,focus,focus_until,"
            "plan_id,knobs,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,course) DO UPDATE SET "
            "mode=excluded.mode, introductions=excluded.introductions, "
            "intro_axis=excluded.intro_axis, debt=excluded.debt, "
            "focus=excluded.focus, focus_until=excluded.focus_until, "
            "plan_id=excluded.plan_id, knobs=excluded.knobs, "
            "updated_at=excluded.updated_at",
            (
                user_id,
                course,
                style.mode,
                style.introductions,
                style.intro_axis,
                style.debt,
                style.focus,
                style.focus_until,
                style.plan_id,
                json.dumps(style.knobs, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        con.execute(
            "INSERT INTO style_revisions(user_id,course,changed_at,snapshot) VALUES(?,?,?,?)",
            (user_id, course, _now(), style.snapshot()),
        )
    return style


def from_mode(name: str) -> Style:
    """The recipe a named mode stands for."""
    try:
        return MODES[name]
    except KeyError:
        raise BadStyle(f"unknown mode {name!r}; available: {', '.join(sorted(MODES))}") from None


def named(style: Style) -> Style:
    """
    The style with its `mode` telling the truth.

    A style that matches a preset keeps the preset's name; one that does not is
    `wlasny`, however it got there. Comparing the recipe rather than trusting the
    stored name means a mode whose definition changes later cannot leave somebody
    labelled with a recipe they are not running.
    """
    bare = replace(style, mode="")
    for name, recipe in MODES.items():
        if replace(recipe, mode="") == bare:
            return replace(style, mode=name)
    return replace(style, mode=CUSTOM)


def latest_revision(
    con: sqlite3.Connection, course: str, *, user_id: int = DEFAULT_USER
) -> int | None:
    """
    The revision an answer given right now should be filed under.

    `None` when the person has never saved a style, which is not a gap: they are
    studying the default, and the default is what the engine does with no row at
    all. An answer with neither revision set is an answer given under no stated
    intent, and that is a true thing to record.
    """
    row = con.execute(
        "SELECT id FROM style_revisions WHERE user_id = ? AND course = ? ORDER BY id DESC LIMIT 1",
        (user_id, course),
    ).fetchone()
    return int(row["id"]) if row else None


def history(
    con: sqlite3.Connection, course: str, *, user_id: int = DEFAULT_USER
) -> list[tuple[int, str, str]]:
    """Every revision, oldest first: `(id, changed_at, snapshot)`."""
    return [
        (int(r["id"]), r["changed_at"], r["snapshot"])
        for r in con.execute(
            "SELECT id, changed_at, snapshot FROM style_revisions "
            "WHERE user_id = ? AND course = ? ORDER BY id",
            (user_id, course),
        )
    ]
