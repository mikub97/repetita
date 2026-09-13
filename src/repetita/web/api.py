"""
The HTTP surface: three JSON endpoints and the page that drives them.

Grading happens here and only here. The client posts what the learner did, the
server decides what it was worth, and the verdict comes back with the answer
attached -- so the browser cannot know whether it was right before it asks. Any
client-side grading would need the answer in the page, which is the thing
invariant 2 forbids.
"""

from __future__ import annotations

import random
import sqlite3
import tempfile
from contextlib import ExitStack, suppress
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .. import __version__, graders, policies, presenters, srs
from ..content.facets import family_of
from ..content.labels import derive as derive_label
from ..content.loader import expand_cards
from ..content.models import Course
from ..content.validate import check
from ..core.buckets import ORDER as BUCKET_ORDER
from ..core.forms import GRADER_FORMS
from ..core.protocols import GradingOptions
from ..core.types import Response as Answer
from ..importers.emit import FIELD_ORDER
from ..policies import context, daily
from ..policies import planned as planned_policy
from ..store import browse as store_browse
from ..store import cards as store_cards
from ..store import catalogue as store_catalogue
from ..store import containers as store_containers
from ..store import drafts as store_drafts
from ..store import feedback as store_feedback
from ..store import issues as store_issues
from ..store import material as store_material
from ..store import plans as store_plans
from ..store import reports as store_reports
from ..store import reviews
from ..store import styles as store_styles
from ..store import users as store_users
from ..store.users import UnknownUser
from .auth import current_user, guard
from .auth import db as auth_db
from .serialize import (
    MIN_CHOICE_OPTIONS,
    MIN_WORDBANK_TOKENS,
    SUPPORTED_FORMS,
    answer_tokens,
    flag_for,
    public_card,
    revealed,
    served_form,
)

if TYPE_CHECKING:  # `app` imports this module, so the real import would cycle.
    from .app import Library, Shelf

#: How many distractors to fetch per card. More than a question needs, so a
#: changed answer colliding with one does not leave the choice short.
DISTRACTOR_POOL = 6

bp = Blueprint(
    "repetita",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)


#: Nobody signed in, nobody gets in. On the blueprint itself, so it holds
#: however this application is mounted -- and a no-op wherever there is no login
#: to offer, which is every host and every database without a password.
bp.before_request(guard)


class ApiError(Exception):
    """A refusal with a machine-readable reason.

    The reason is a code rather than a sentence because UI text is translated
    (CLAUDE.md: the engine ships no UI strings as Python literals) and because a
    client that has to match on prose is a client that breaks on a typo fix.
    """

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@bp.app_errorhandler(ApiError)
def _on_api_error(error: ApiError) -> tuple[Response, int]:
    return jsonify({"error": error.code}), error.status


# --- request plumbing -----------------------------------------------------


#: One connection per request, opened in `auth` so that the sign-in path and
#: everything behind it share it rather than opening two.
_db = auth_db


def _user_id() -> int:
    """
    Who this request is for.

    One place, which is the point: every query that scopes by person asks here,
    so where the answer comes from -- a host's `identity`, a session cookie, or
    the owner because nobody has a password yet -- is `auth`'s business and not
    each of the fifty call sites'.

    Sits beside `_course()` on purpose: the two questions a request has to
    answer before it can look anything up are "which course" and "whose".
    """
    return current_user().id


def _course() -> str:
    """
    Which course this request is about.

    The client names it -- `api.js` puts it on every call the way it already puts
    the mount prefix on every path. When nothing names one, the configured
    default answers, so `curl`, the CLI and every existing test keep working
    without knowing courses exist.

    Deliberately not server-side "current course" state. One process serving two
    browsers with different flags open is not a thing this app has to handle
    today, but making the answer depend on the last click anybody made would be a
    bug that only appears when it does.
    """
    named = request.args.get("course") or request.headers.get("X-Repetita-Course")
    if named:
        return str(named)
    # Nobody named one: the course last chosen, then the configured default.
    # The remembered answer comes first so that a browser with no localStorage
    # -- a fresh profile, a cleared cache -- still opens where the last session
    # left off rather than back at whatever the launcher happened to pass.
    remembered = store_containers.last_course(_db(), user_id=_user_id())
    if remembered and remembered in store_cards.courses_in_db(_db()):
        return remembered
    return str(current_app.config["REPETITA_COURSE_ID"])


def _shelf() -> Shelf:
    shelf: Shelf = current_app.extensions["repetita"]
    return shelf


def _library() -> Library:
    # Deciding which course this is about involves asking who is asking, and
    # `UnknownUser` is a `LookupError` -- so a host whose `identity` names an
    # account that does not exist used to arrive here and be reported as
    # `unknown_course`, which is a misleading answer to a question nobody asked.
    # Whose it is gets settled first, and its failure is allowed to be loud.
    _user_id()
    try:
        return _shelf().get(_course())
    except UnknownUser:
        raise
    except LookupError as e:
        # A course id the database does not hold -- a stale localStorage entry
        # after a course was renamed, or a hand-typed query string.
        raise ApiError("unknown_course", 404) from e


def _payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _day(body: dict[str, Any] | None = None) -> date:
    """
    The learner's calendar day, which is not the server's.

    Taken from the request when offered, because deriving it from the instant is
    how a session studied in one timezone gets filed under another's tomorrow --
    a bug this codebase has already shipped once, and the reason `record_answer`
    takes the day and the instant as separate arguments.
    """
    raw = request.args.get("day") or (body or {}).get("day")
    if raw in (None, ""):
        return datetime.now().date()
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        # Refused, not ignored: silently substituting today would move a whole
        # session's rows to the wrong date with nothing to show for it.
        raise ApiError("bad_day") from None


def _requested_plan(con: sqlite3.Connection, body: dict[str, Any] | None = None) -> Any:
    """
    The plan this request is studying under, if it named one.

    Named per request rather than read from a global "active plan": the Study tab
    and the designer's own practice are two paths through one set of material,
    and which one you are on is a property of what you asked for.
    """
    raw = (body or {}).get("plan") if body else request.args.get("plan")
    if raw in (None, "", "null"):
        return None
    try:
        plan = store_plans.get(con, int(raw), user_id=_user_id())
    except (TypeError, ValueError):
        raise ApiError("plan must be an id", 400) from None
    if plan is None:
        # A well-formed id for a plan that is not there. Falling back to the
        # plain session would answer a question nobody asked, and would file the
        # answers with no revision at all -- so the one endpoint that can tell
        # you the plan is gone would be the one that says nothing.
        raise ApiError("unknown_plan", 404)
    return plan


def _recipe(con: sqlite3.Connection, today: Any, course: str) -> Any:
    """
    The saved style, looked up. `None` when there is nothing to look up.

    `None` rather than a default `Recipe` so the policy short-circuits on the
    common path instead of walking a recipe that says "do what you already do".
    A style the course cannot honour -- an axis it does not declare ordered --
    raises, and the caller turns that into a 400 a person can read.
    """
    style = store_styles.get(con, course, user_id=_user_id())
    if style == store_styles.DEFAULT:
        return None
    try:
        return policies.recipe_for(con, style, today, course=course, user_id=_user_id())
    except store_styles.BadStyle as bad:
        raise ApiError(str(bad), 400) from bad


def _presenter(plan: Any = None) -> Any:
    """
    The presenter this request asks its cards through, resolved once.

    Once, because `served_form` is called twice for a single answer -- when the
    question is served and again when the answer is recorded as "what was
    actually served" -- and resolving it separately in the two places is how the
    review log comes to disagree with the screen the learner saw. A test pins
    that the two agree; this function is the reason it can.
    """
    steps = None
    knobs = getattr(plan, "knobs", None)
    if knobs is not None:
        raw = knobs.get("ladder_steps")
        if not isinstance(raw, bool) and isinstance(raw, (int, float, str)):
            try:
                steps = int(raw)
            except (TypeError, ValueError):
                steps = None
    return presenters.get(steps=steps)


def _presenter_for(con: sqlite3.Connection, body: dict[str, Any] | None = None) -> Any:
    """The presenter this request asks through, whether a plan or a style set it."""
    plan = _requested_plan(con, body)
    if plan is not None:
        return _presenter(plan)
    return _presenter(store_styles.get(con, _course(), user_id=_user_id()))


def _revision_for(con: sqlite3.Connection, body: dict[str, Any]) -> tuple[int | None, int | None]:
    """
    Which revision served this answer, as `(plan, style)`.

    The client says *which plan* it was practising; the server decides which
    revision that is. Same reason `form` is recomputed on the way in: the log is
    a record of what happened, and a client is free to lie.

    Exactly one of the two, never both. An answer given under a plan is evidence
    about that plan; an answer given on the Study tab is evidence about how that
    person has their queue set up. Recording one as the other is the comparison
    failure ADR-0007 names, arriving by a different door.
    """
    plan = _requested_plan(con, body)
    if plan is not None:
        return store_plans.latest_revision(con, plan.id, user_id=_user_id()), None
    return None, store_styles.latest_revision(con, _course(), user_id=_user_id())


def _grading(course: Course) -> GradingOptions:
    spec = course.grading
    return GradingOptions(
        fold_accents=spec.fold_accents,
        ignore_punctuation=spec.ignore_punctuation,
        ignore_case=spec.ignore_case,
        sentence_slack=spec.sentence_slack,
    )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _ms(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- routes ---------------------------------------------------------------


@bp.get("/")
def index() -> str:
    return render_template("index.html")


#: The skins the interface can wear. Names, not numbers: a stored `2` would mean
#: nothing the day one is added in the middle. The default declares no tokens of
#: its own -- it is what `:root` already says.
THEMES = ("spokojny", "duzy", "cieply")


@bp.get("/api/settings")
def settings() -> Response:
    """What this person has chosen. Defaults, never an error."""
    saved = store_containers.settings(_db(), store_containers.UI, user_id=_user_id())
    theme = str(saved.get("theme") or THEMES[0])
    return jsonify({"theme": theme if theme in THEMES else THEMES[0], "themes": list(THEMES)})


@bp.post("/api/settings")
def save_settings() -> Response:
    """
    Remember a choice. Purely how the app looks; nothing here changes what it does.

    Stored beside the browser's own copy rather than instead of it: the browser
    is what paints the right theme on the first frame, and this is what survives
    a cleared cache or answers a second browser on the same machine.
    """
    body = _payload()
    theme = str(body.get("theme") or "")
    if theme not in THEMES:
        raise ApiError("unknown_theme", 400)
    store_containers.remember(_db(), store_containers.UI, {"theme": theme}, user_id=_user_id())
    return jsonify({"theme": theme})


@bp.post("/api/feedback")
def feedback() -> Response:
    """
    A written comment about anything, saved where it can be committed.

    Not a card report and not a material issue -- those are claims about one
    exercise and about how material is grouped. This is the third thing, the one
    with no shape: "I do not understand why this came back", "the button is in
    the wrong place", "I stopped because it got boring". A prototype being
    tested by three people needs somewhere for that to go, and it has to be a
    place the author can read later without asking anybody to export anything.
    """
    body = _payload()
    try:
        path = store_feedback.save(
            str(body.get("text") or ""),
            course_dir=current_app.config.get("REPETITA_COURSE"),
            where=str(body.get("where") or ""),
            course=_course(),
            version=__version__,
        )
    except store_feedback.Empty:
        raise ApiError("empty_feedback", 400) from None
    except OSError as e:
        # Read-only checkout, a full disk, a path that is not writable. The
        # learner typed something and it is gone either way -- say so rather
        # than pretend it was kept.
        raise ApiError("feedback_not_saved", 500) from e
    return jsonify({"saved": path.name, "from": store_feedback.who()})


@bp.get("/api/courses")
def courses() -> Response:
    """
    Every course in the database, for the picker.

    `owed` per course is the reason this is not just a list of names: the whole
    point of a picker is deciding which one to open, and "14 waiting" is what
    decides it. It costs one queue read per course, which is why the picker is
    not on the critical path of anything else.

    Courses the database holds but cannot build -- a broken exercise type, say --
    are listed with what is known and marked, rather than omitted. A course that
    vanishes from the picker is a course nobody can reach to fix.

    **Every course, each marked with whether you are enrolled.** Not a filtered
    list: the picker needs the rest to offer them, and a course you cannot see
    is a course you cannot join. Absence from `enrolments` is "not on my flag
    picker", never "cannot see it" -- material is shared and visible (ADR-0008).

    An account enrolled in *nothing* gets everything, and `enrolled` says so.
    That is every fresh install and every database that predates accounts, where
    filtering to an empty list would leave a picker with nothing in it and an app
    with no course to open. Enrolment starts mattering when you make the first
    one -- the same shape as the login appearing with the first password.
    """
    con, today = _db(), _day()
    mine = set(store_users.enrolments(con, _user_id()))
    out = []
    for course_id in store_cards.courses_in_db(con):
        try:
            course = store_cards.course_from_db(con, course_id)
        except LookupError:  # pragma: no cover -- it was listed a line ago
            continue
        out.append(
            {
                "id": course_id,
                "title": course.title,
                "l1": course.l1.code,
                "l2": course.l2.code,
                "flag": flag_for(course.l2.code, course.l2.variant),
                "enrolled": course_id in mine if mine else True,
                "owed": daily.owed_count(con, today, course=course_id, user_id=_user_id()),
                "notes": con.execute(
                    "SELECT COUNT(*) AS n FROM notes WHERE course = ? AND archived_at IS NULL",
                    (course_id,),
                ).fetchone()["n"],
            }
        )
    # Whether enrolment is being honoured at all, so the picker knows if the
    # "other courses" section means anything.
    return jsonify({"courses": out, "selected": _course(), "enrolling": bool(mine)})


@bp.post("/api/courses/<course_id>/join")
def join_course(course_id: str) -> Response:
    """
    Put a course on your flag picker.

    Joining takes no permission: the material is shared, and the only thing an
    enrolment changes is which flags you see. What it must not do is disturb any
    history -- rejoining a course you left has to be rejoining, not starting
    again -- which is why it writes to `enrolments` and to nothing else.
    """
    if course_id not in store_cards.courses_in_db(_db()):
        raise ApiError("unknown_course", 404)
    store_users.enrol(_db(), _user_id(), course_id)
    return jsonify({"joined": course_id})


@bp.post("/api/courses/<course_id>/leave")
def leave_course(course_id: str) -> Response:
    """Take it off the picker. The history stays, so coming back is coming back."""
    store_users.unenrol(_db(), _user_id(), course_id)
    return jsonify({"left": course_id})


@bp.post("/api/courses/<course_id>/select")
def select_course(course_id: str) -> Response:
    """
    Remember that this is the course being studied.

    The browser remembers too, in `localStorage`, and that is what the next page
    load reads -- this is the copy that survives clearing site data, and the one
    the CLI and a second browser see. Two places because they answer different
    questions: "what was I looking at in this tab" and "what is this database
    for".
    """
    con = _db()
    if course_id not in store_cards.courses_in_db(con):
        raise ApiError("unknown_course", 404)
    store_containers.touch(con, course_id, user_id=_user_id())
    return jsonify({"selected": course_id})


@bp.get("/api/state")
def state() -> Response:
    """Counters, and nothing that could answer a question."""
    con, lib, today = _db(), _library(), _day()
    course = lib.course.id
    return jsonify(
        {
            "day": today.isoformat(),
            "course": {
                "id": lib.course.id,
                "title": lib.course.title,
                "l1": lib.course.l1.code,
                "l2": lib.course.l2.code,
            },
            "scheduler": lib.course.scheduler,
            # The debt, not the size of the batch. One number per idea: the two
            # disagreed in the predecessor and the tile pinned while the
            # per-card counter kept moving.
            "owed": daily.owed_count(con, today, course=course, user_id=_user_id()),
            "answered_today": reviews.count_on(con, today, course=course, user_id=_user_id()),
            "target": daily.DAILY_TARGET,
            "done": daily.day_done(con, today, course=course, user_id=_user_id()),
            "gate_open": daily.gate_open(
                reviews.recent_ratings(con, daily.GATE_WINDOW, course=course, user_id=_user_id())
            ),
            "cards": len(lib.cards),
            # Cards taken out on the learner's word rather than on evidence.
            # Surfaced because a claim nobody can see is a claim nobody can
            # revisit, and a mis-click would otherwise be invisible forever.
            "declared": store_cards.declared_count(con, course=course, user_id=_user_id()),
            # Exercises reported broken and not yet dealt with. Same argument as
            # `declared`: a report nobody can see is a report nobody acts on.
            "reports_open": store_reports.open_report_count(
                con, course=lib.course.id, user_id=_user_id()
            ),
            "quarantined": lib.quarantined,
            # How the queue is being built, so every screen can say so. `owed`
            # above is untouched and is still the whole debt -- `owed_count` has
            # no parameter a focus could reach it through.
            "style": _style_state(con, today, course),
        }
    )


def _style_state(con: sqlite3.Connection, today: Any, course: str) -> dict[str, Any]:
    """The mode's name, and what a focus is keeping back right now."""
    style = store_styles.get(con, course, user_id=_user_id())
    active = style.active_focus(today)
    out: dict[str, Any] = {
        "mode": style.mode,
        "focus": style.focus,
        "focus_until": style.focus_until,
        # Set when a focus is written down but has run out. The screen says so
        # and offers to renew it, rather than a setting silently ceasing to
        # apply while still sitting in the row.
        "lapsed": bool(style.focus) and not active,
        "hidden": 0,
    }
    if active:
        try:
            recipe = policies.recipe_for(con, style, today, course=course, user_id=_user_id())
        except store_styles.BadStyle:
            return out
        if recipe.focus_ids is not None:
            out["hidden"] = daily.owed_hidden(
                con, today, recipe.focus_ids, course=course, user_id=_user_id()
            )
    return out


@bp.get("/api/session")
def session() -> Response:
    """Today's queue, every card serialised with its question open."""
    con, lib, today = _db(), _library(), _day()
    # Without `?plan=`, this is the course's own path and nothing else -- having
    # a plan does not change it. A plan is an *additional* way through the same
    # material, so it has to be asked for; a learner who builds one and dislikes
    # it should not have to delete it to get their ordinary session back.
    study_plan = _requested_plan(con)
    policy = policies.get("planned" if study_plan else None)
    # The Study tab's own settings. A named plan still wins -- ADR-0007's "a
    # session is built under a plan only when the request names one" is untouched
    # -- and `?style=none` builds the default, which is what the "what would I
    # have without this" button asks for.
    recipe = None
    if study_plan is None and request.args.get("style") != "none":
        recipe = _recipe(con, today, lib.course.id)
    plan = policy.build(
        con,
        today,
        plan=study_plan,
        course=lib.course.id,
        user_id=_user_id(),
        recipe=recipe,
    )
    rng = random.Random()
    presenter = _presenter(study_plan or store_styles.get(con, lib.course.id, user_id=_user_id()))
    # One read for the whole queue rather than one per card: the presenter needs
    # each card's history to decide how to ask it.
    states = store_cards.all_states(con, course=lib.course.id, user_id=_user_id())

    cards = []
    for card_id in plan.cards:
        card = lib.cards.get(card_id)
        if card is None:
            continue
        note = lib.notes.get(card.note_id)
        notetype = lib.notetypes.get(card.notetype)
        if note is None or notetype is None:
            continue
        payload = public_card(
            card,
            note,
            notetype,
            handle=lib.handles.handle(card_id),
            rng=rng,
            state=states.get(card_id),
            distractors=store_cards.distractors_for(con, card_id, DISTRACTOR_POOL),
            presenter=presenter,
        )
        # Added here rather than inside `public_card`, which stays the single
        # filter over an open question and is not worth loosening for this.
        # "You have not seen this before" is a fact about the learner, not about
        # the material, and reveals nothing that could answer the question.
        state = states.get(card_id)
        payload["fresh"] = state is None or state.is_new
        cards.append(payload)

    return jsonify(
        {
            "day": today.isoformat(),
            "cards": cards,
            "has_more": plan.has_more,
            "consolidating": plan.consolidating,
            # Cards held back because a sibling is in this session. Reported
            # so a queue shorter than the debt has a visible reason.
            "buried": plan.buried,
            # And cards a focus excluded, with the selector that did it. Sent
            # whenever a focus is set, including when it hides nothing today: a
            # guardrail that goes quiet while it is not biting is one you forget
            # you turned on (ADR-0018).
            "hidden": plan.hidden,
            "focus": plan.focus,
        }
    )


@bp.post("/api/reload")
def reload_content() -> Response:
    """
    Rebuild what is served from the database, without restarting.

    This is what makes "tonight's lesson, in tonight's queue" possible. Without
    it, adding material means restarting the process, and a restart is exactly
    the moment the content pipeline is least welcome to interrupt.

    It no longer reads the course files. Since ADR-0015 material arrives through
    `repetita import` or through the app, and this picks up whatever they left --
    so the sequence is import, then reload, and each step says what it did. It
    used to be one step that hid the other, and an import is not something to
    perform by accident while refreshing a screen.

    Rejecting a broken course leaves the running one in place. Swapping in a
    half-loaded library and reporting the error afterwards would take the
    learner's material away over a typo in a file they were editing.
    """
    course = _course()
    before = _library()
    _shelf().drop(course)
    try:
        library = _shelf().get(course)
    except (ValueError, LookupError) as broken:
        raise ApiError(str(broken), 422) from broken
    return jsonify(
        {
            "notes": len(library.notes),
            "cards": len(library.cards),
            # What actually changed, rather than "reloaded". A reload that
            # silently loaded nothing looks identical to one that worked.
            "added": sorted(set(library.cards) - set(before.cards))[:20],
            "removed": sorted(set(before.cards) - set(library.cards))[:20],
            "added_total": len(set(library.cards) - set(before.cards)),
            "removed_total": len(set(before.cards) - set(library.cards)),
            "quarantined": library.quarantined,
        }
    )


@bp.post("/api/known")
def known() -> Response:
    """
    "I already know this" -- and taking that back.

    Deliberately not a grade. Answering a card is evidence about memory and moves
    the schedule; this is a statement about the material, and it moves the card
    out of the queue without pretending anything was measured. The review log
    stays a record of answers given, so this writes nothing to it.
    """
    body = _payload()
    con, lib, today = _db(), _library(), _day(body)

    card_id = lib.handles.card(str(body.get("card_id") or ""))
    card = lib.cards.get(card_id) if card_id else None
    if card is None:
        raise ApiError("unknown_card", 404)

    undo = bool(body.get("undo"))
    state = (
        store_cards.undo_known(con, card.id, user_id=_user_id())
        if undo
        else store_cards.declare_known(
            con, card.id, today, backend=srs.get(lib.course.scheduler), user_id=_user_id()
        )
    )

    return jsonify(
        {
            "declared": state.retired_reason == store_cards.DECLARED if state else False,
            "reason": state.retired_reason if state else None,
            "owed": daily.owed_count(con, today, course=lib.course.id, user_id=_user_id()),
            "declared_total": store_cards.declared_count(con, user_id=_user_id()),
        }
    )


@bp.post("/api/report")
def report() -> Response:
    """
    "This exercise is wrong" -- and taking that back.

    A third kind of thing, next to answering and declaring. Answering is evidence
    about the learner; declaring is a claim about what they know; this is a claim
    about the *material*, and the only one of the three that someone has to go
    and fix a file about. So it is recorded where it can be read later with the
    text that provoked it, and it suspends the card meanwhile -- a broken
    exercise should stop costing reviews the moment it is called broken.

    The snapshot is assembled here because this is the last place the authored
    note is in memory: `Note.origin` never reaches the `notes` table, and the
    table itself is rebuilt from the files this report is complaining about.
    """
    body = _payload()
    con, lib, today = _db(), _library(), _day(body)

    card_id = lib.handles.card(str(body.get("card_id") or ""))
    card = lib.cards.get(card_id) if card_id else None
    if card is None:
        raise ApiError("unknown_card", 404)

    if bool(body.get("undo")):
        store_reports.withdraw_report(con, card.id, user_id=_user_id())
        return jsonify(
            {
                "reported": False,
                "owed": daily.owed_count(con, today, course=lib.course.id, user_id=_user_id()),
                "reports_open": store_reports.open_report_count(
                    con, course=lib.course.id, user_id=_user_id()
                ),
            }
        )

    reason = str(body.get("reason") or "")
    if reason not in store_reports.REASONS:
        raise ApiError("bad_reason", 400)

    note = lib.notes[card.note_id]
    notetype = lib.notetypes[card.notetype]
    state = store_cards.get_state(con, card.id, user_id=_user_id())
    # The distractors matter: without them `served_form` cannot return `choice`,
    # and every report about bad options would be filed as a typein problem.
    options = store_cards.distractors_for(con, card.id, DISTRACTOR_POOL)
    store_reports.report_card(
        con,
        card.id,
        reason,
        today,
        backend=srs.get(lib.course.scheduler),
        note=_text(body.get("note")) or None,
        snapshot=store_reports.Snapshot(
            note_id=note.id,
            template=card.template,
            form=served_form(
                card,
                note,
                notetype,
                state=state,
                distractors=options,
                presenter=_presenter_for(con, body),
            ),
            fields=dict(note.fields),
            origin=note.origin,
            unit=note.unit,
        ),
        user_id=_user_id(),
    )

    return jsonify(
        {
            "reported": True,
            "owed": daily.owed_count(con, today, course=lib.course.id, user_id=_user_id()),
            "reports_open": store_reports.open_report_count(
                con, course=lib.course.id, user_id=_user_id()
            ),
        }
    )


@bp.post("/api/answer")
def answer() -> Response:
    """Grade one answer, schedule the card, log it, and reveal what was hidden."""
    body = _payload()
    con, lib, today = _db(), _library(), _day(body)

    # The client posts back the opaque handle it was given, never a card id.
    # An unknown handle is the expected outcome after a restart, when the whole
    # mapping is regenerated; the client refetches, which is right anyway since
    # the content may have changed under it.
    card_id = lib.handles.card(str(body.get("card_id") or ""))
    card = lib.cards.get(card_id) if card_id else None
    if card is None:
        raise ApiError("unknown_card", 404)
    note = lib.notes[card.note_id]
    notetype = lib.notetypes[card.notetype]
    accepted = note.answers(notetype.cards[card.template].expect)

    given = Answer(
        text=_text(body.get("text")),
        choice=_text(body.get("choice")),
        ms=_ms(body.get("ms")),
    )
    judgement = graders.get(card.grader).grade(given, accepted, opts=_grading(lib.course))

    # Read before recording: the form is a fact about the question that was put,
    # and `record_answer` is about to make this card one answer older.
    before = store_cards.get_state(con, card.id, user_id=_user_id())
    _revisions = _revision_for(con, body)

    state_after = reviews.record_answer(
        con,
        card.id,
        judgement.rating,
        backend=srs.get(lib.course.scheduler),
        at=datetime.now(UTC),
        local_day=today,
        mode="session",
        # What was actually served, recomputed rather than taken from the client:
        # the log is a record of what happened, and a client is free to lie.
        form=served_form(card, note, notetype, state=before, presenter=_presenter_for(con, body)),
        # Wrong answers too. In a year these are the best distractors available,
        # because they are the mistakes real learners made.
        answer=given.text or given.choice,
        duration_ms=given.ms,
        # Which revision of which plan, or of which study style, chose to serve
        # this card. Recorded now because it cannot be reconstructed later --
        # ADR-0003, and a column added in six months leaves everything before it
        # unattributable.
        plan_revision_id=_revisions[0],
        style_revision_id=_revisions[1],
        user_id=_user_id(),
    )

    return jsonify(
        {
            "rating": int(judgement.rating),
            "passed": judgement.passed,
            "matched": judgement.matched,
            "diff": [
                {"given": t.given, "expected": t.expected, "kind": t.kind} for t in judgement.diff
            ],
            # Past tense: the question is closed, so the answer may be shown.
            "answers": accepted,
            "reveal": revealed(note, notetype, card.template),
            "due": state_after.due,
            "interval": state_after.interval,
            "owed": daily.owed_count(con, today, course=lib.course.id, user_id=_user_id()),
            "answered_today": reviews.count_on(
                con, today, course=lib.course.id, user_id=_user_id()
            ),
        }
    )


# --- how I study ----------------------------------------------------------
#
# The Study tab's own settings, which is a different thing from a study plan and
# deliberately a different screen (ADR-0017). A plan is an additional path
# through the material and is asked for per request; this configures the one path
# everybody already has. ADR-0007 reverted the version that merged them.
#
# The same rule as the catalogue applies here: counts and labels, never a card
# id and never a note field. The preview leans on `_preview_names`, which is the
# one deliberate loosening and is argued where it is defined.


def _style_from(body: dict[str, Any], current: store_styles.Style) -> store_styles.Style:
    """
    The style a PUT is asking for, built on the one that is there.

    A named `mode` replaces the whole recipe; anything else edits the current one
    field by field. That is what makes "pick Nadrabianie, then nudge the batch"
    work without the client having to reconstruct a mode's other four settings.
    """
    if "mode" in body and body["mode"] and body["mode"] != store_styles.CUSTOM:
        try:
            current = store_styles.from_mode(str(body["mode"]))
        except store_styles.BadStyle as bad:
            raise ApiError(str(bad), 400) from bad

    knobs = dict(current.knobs)
    if "knobs" in body:
        raw = body["knobs"]
        if not isinstance(raw, dict):
            raise ApiError("knobs must be an object", 400)
        # `None` removes a knob rather than storing a null, so "put this back to
        # the default" is expressible and does not need a second endpoint.
        for key, value in raw.items():
            if value is None:
                knobs.pop(key, None)
            else:
                knobs[key] = value

    plan_id = current.plan_id
    if "plan_id" in body:
        plan_id = None if body["plan_id"] in (None, "", "null") else int(body["plan_id"])

    focus = str(body.get("focus", current.focus) or "")
    until = body.get("focus_until", current.focus_until)
    until = None if until in ("", "null") else until
    # Dropping the focus drops its expiry with it, so a focus set again later
    # cannot inherit a date from one somebody turned off months ago.
    if not focus:
        until = None

    return store_styles.named(
        store_styles.Style(
            mode=current.mode,
            introductions=str(body.get("introductions", current.introductions)),
            intro_axis=str(body.get("intro_axis", current.intro_axis)),
            debt=str(body.get("debt", current.debt)),
            focus=focus,
            focus_until=str(until) if until is not None else None,
            plan_id=plan_id,
            knobs=knobs,
        )
    )


def _style_payload(style: store_styles.Style) -> dict[str, Any]:
    return {
        "mode": style.mode,
        "introductions": style.introductions,
        "intro_axis": style.intro_axis,
        "debt": style.debt,
        "focus": style.focus,
        "focus_until": style.focus_until,
        "plan_id": style.plan_id,
        "knobs": style.knobs,
    }


@bp.get("/api/style")
def get_style() -> Response:
    """How this person studies this course, and what else they could choose."""
    con, lib, today = _db(), _library(), _day()
    course = lib.course.id
    style = store_styles.get(con, course, user_id=_user_id())
    axes = context.ordered_axes(con, course)
    return jsonify(
        {
            "style": _style_payload(style),
            "default": _style_payload(store_styles.DEFAULT),
            "modes": [
                {"key": key, **_style_payload(recipe)} for key, recipe in store_styles.MODES.items()
            ],
            "orderings": list(policies.ORDERINGS),
            # Every axis a selector can name, for building a focus out of chips
            # rather than making somebody type `topic=comida,state=new`. Selector
            # syntax in an interface is a leaked implementation.
            "focus_axes": [
                {"axis": name, "title": axis.title, "values": sorted(axis.values)}
                for name, axis in store_cards.facets_from_db(con, course).axes.items()
            ],
            "debt_orderings": list(policies.DEBT_ORDERINGS),
            # Only the axes the course puts an order on. `topic` is a set of
            # names with no sequence, and offering it here would make "easiest
            # first" mean "alphabetically first" -- the bug this all began with.
            "axes": sorted(axes),
            "templates": sorted(
                r["template"]
                for r in con.execute(
                    "SELECT DISTINCT c.template AS template FROM cards c "
                    "JOIN notes n ON n.id = c.note_id "
                    "WHERE n.course = ? AND c.archived_at IS NULL",
                    (course,),
                )
            ),
            "owed": daily.owed_count(con, today, course=course, user_id=_user_id()),
            # A consumer at last for a function that has computed this since it
            # was written. It is the consequence display: a knob that grows the
            # backlog should say so while you are turning it.
            "forecast": daily.forecast(con, today, course=course, user_id=_user_id()),
        }
    )


@bp.put("/api/style")
def put_style() -> Response:
    """Save it, and record what it was."""
    con, lib, today = _db(), _library(), _day()
    course = lib.course.id
    wanted = _style_from(_payload(), store_styles.get(con, course, user_id=_user_id()))
    try:
        # Validated against the course before it is stored, not after: a style
        # naming an axis this course does not order is refused with a sentence
        # rather than saved and then failing on the next session build.
        policies.recipe_for(con, wanted, today, course=course, user_id=_user_id())
        saved = store_styles.save(con, course, wanted, user_id=_user_id())
    except store_styles.BadStyle as bad:
        raise ApiError(str(bad), 400) from bad
    return jsonify({"style": _style_payload(saved)})


@bp.post("/api/style/preview")
def preview_style() -> Response:
    """
    What tomorrow would look like under a style that has not been saved.

    The body is a whole style rather than an id, which is the one thing this has
    over the plan preview: you can look before you commit. It is cheap because
    the policies are pure -- the same argument ADR-0007 makes, applied to the
    screen where it matters more.
    """
    con, lib, today = _db(), _library(), _day()
    course = lib.course.id
    body = _payload()
    budget = max(1, min(200, int(body.get("budget") or 20)))
    wanted = _style_from(body, store_styles.get(con, course, user_id=_user_id()))
    try:
        recipe = policies.recipe_for(con, wanted, today, course=course, user_id=_user_id())
    except store_styles.BadStyle as bad:
        raise ApiError(str(bad), 400) from bad

    session = policies.get().build(
        con, today, limit=budget, course=course, user_id=_user_id(), recipe=recipe
    )
    by_unit: dict[str, int] = {}
    for card_id in session.cards:
        card = lib.cards.get(card_id)
        note = lib.notes.get(card.note_id) if card else None
        if note is not None:
            by_unit[note.unit] = by_unit.get(note.unit, 0) + 1

    return jsonify(
        {
            "style": _style_payload(wanted),
            "budget": budget,
            "picked": len(session.cards),
            "by_unit": by_unit,
            "names": _preview_names(con, session.cards),
            "consolidating": session.consolidating,
            "buried": session.buried,
            "hidden": session.hidden,
            "plan_missing": recipe.plan_missing,
            # The true debt, always. The second curve is what this style would
            # actually serve, and the gap between them is what a focus costs --
            # which is the number worth looking at while you are setting one.
            "forecast": daily.forecast(con, today, course=course, user_id=_user_id()),
            "forecast_focused": (
                daily.forecast(
                    con, today, course=course, user_id=_user_id(), focus_ids=recipe.focus_ids
                )
                if recipe.focus_ids is not None
                else None
            ),
        }
    )


# --- designing a course of study ------------------------------------------
#
# ADR-0005 is the governing constraint here, and it is easy to break by
# accident: "anything added later that carries a card id to the client -- a deep
# link, an error message, a debug endpoint -- reopens this." A catalogue is
# exactly that shape of thing. Card ids are authored from the material, so a
# quarter of them *are* the answer.
#
# So these endpoints return counts and labels. Never a card id, never a note id,
# never a note field. The one place material reaches the client is the session
# itself, through `public_card` and a handle, unchanged.


def _selector(raw: str | None) -> dict[str, list[str]]:
    try:
        return store_catalogue.parse_selector(raw)
    except store_catalogue.SelectorError as e:
        raise ApiError(str(e), 400) from None


def _plan_json(plan: store_plans.Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "course": plan.course,
        "active": plan.active,
        "priorities": [
            {"rank": p.rank, "axis": p.axis, "value": p.value, "weight": p.weight}
            for p in plan.priorities
        ],
        "knobs": plan.knobs,
    }


@bp.get("/api/catalogue")
def catalogue() -> Response:
    """
    How much material there is, grouped however you ask.

    Counts only. This is what the lesson designer draws its rows from, and it
    must never become a way to read the course.
    """
    con, course = _db(), _library().course.id
    group_by = [
        d.strip() for d in (request.args.get("group_by") or "topic").split(",") if d.strip()
    ]
    # This tab is about one course, like every other. It was the only screen PR
    # #71 did not scope, which is how the Design tab came to show Portuguese
    # topics while the Italian flag was up. `course` is already a built-in
    # dimension of the selector, so this is the existing filter doing its job
    # rather than a second one beside it.
    where = _selector(request.args.get("where"))
    where.setdefault("course", [course])

    search = (request.args.get("q") or "").strip()
    rows = store_catalogue.catalogue(
        con, group_by=group_by, where=where, text=search or None, user_id=_user_id()
    )

    # With a search on, the rows are the matches. The unfiltered counts come
    # from a second pass so a row can say "12, of which 3 match" -- the total is
    # what tells you whether three is most of the topic or a corner of it.
    totals: dict[str, int] = {}
    if search and len(group_by) == 1:
        totals = {
            r.keys[group_by[0]]: r.notes
            for r in store_catalogue.catalogue(
                con, group_by=group_by, where=where, user_id=_user_id()
            )
        }

    axes = [
        {
            "axis": r["axis"],
            "title": _json_or(r["title"], {}),
            "ordered": bool(r["ordered"]),
            "catch_all": bool(r["catch_all"]),
        }
        for r in con.execute("SELECT * FROM facet_axes WHERE course = ? ORDER BY ord", (course,))
    ]
    # How well each group is known, when there is a single dimension to hang it
    # on. Composition rather than a single number: a set where everything was
    # marked "I know this" and one that was genuinely learned are not the same
    # thing, and the interface has to be able to show the difference.
    mastery = {}
    if len(group_by) == 1:
        mastery = {
            value: {
                "total": m.total,
                "untouched": m.untouched,
                "working": m.working,
                "earned": m.earned,
                "declared": m.declared,
                "suspended": m.suspended,
                "progress": round(m.progress, 3),
                "state": m.state,
            }
            for value, m in store_catalogue.mastery_by(
                con, group_by[0], where={"course": [course]}, user_id=_user_id()
            ).items()
        }

    return jsonify(
        {
            "group_by": group_by,
            "axes": axes,
            "mastery": mastery,
            "q": search,
            # `matched` only when something was searched for. Without it a client
            # cannot tell "3 notes in this topic" from "3 of this topic's 12
            # notes matched", and those read very differently.
            "rows": [
                {
                    **r.keys,
                    "cards": r.cards,
                    "notes": totals.get(r.keys.get(group_by[0], ""), r.notes)
                    if totals
                    else r.notes,
                    **({"matched": r.notes} if search else {}),
                }
                for r in rows
            ],
        }
    )


def _json_or(raw: Any, fallback: Any) -> Any:
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


@bp.get("/api/plans")
def list_plans() -> Response:
    return jsonify(
        {"plans": [_plan_json(p) for p in store_plans.all_plans(_db(), user_id=_user_id())]}
    )


@bp.post("/api/plans")
def create_plan() -> Response:
    body = _payload()
    name = str(body.get("name") or "").strip()
    if not name:
        raise ApiError("a plan needs a name", 400)
    lib = _library()
    course = lib.course.id if lib.course else ""
    plan = store_plans.create(
        _db(), name, course, active=bool(body.get("active")), user_id=_user_id()
    )
    return jsonify(_plan_json(plan))


@bp.put("/api/plans/<int:plan_id>")
def update_plan(plan_id: int) -> Response:
    body = _payload()
    con = _db()
    if store_plans.get(con, plan_id, user_id=_user_id()) is None:
        raise ApiError("unknown_plan", 404)

    if "priorities" in body:
        rows = body.get("priorities") or []
        try:
            priorities = [
                store_plans.Priority(
                    rank=i,
                    axis=str(r["axis"]),
                    value=str(r["value"]),
                    weight=None if r.get("weight") in (None, "") else float(r["weight"]),
                )
                for i, r in enumerate(rows)
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise ApiError(f"bad priority list: {e}", 400) from None
        store_plans.set_priorities(con, plan_id, priorities, user_id=_user_id())

    if "knobs" in body:
        try:
            store_plans.set_knobs(con, plan_id, dict(body["knobs"] or {}), user_id=_user_id())
        except (TypeError, ValueError) as e:
            raise ApiError(str(e), 400) from None

    if body.get("active"):
        store_plans.activate(con, plan_id, user_id=_user_id())

    plan = store_plans.get(con, plan_id, user_id=_user_id())
    assert plan is not None
    return jsonify(_plan_json(plan))


@bp.delete("/api/plans/<int:plan_id>")
def delete_plan(plan_id: int) -> Response:
    # The store refuses somebody else's plan outright. Here that becomes the
    # same 404 every other plan route gives, because from where the caller
    # stands there is no such plan -- and a 500 would say the opposite: that
    # there is one, and something went wrong reaching it.
    try:
        store_plans.delete(_db(), plan_id, user_id=_user_id())
    except store_plans.NotYours:
        raise ApiError("unknown_plan", 404) from None
    return jsonify({"deleted": plan_id})


@bp.post("/api/plans/<int:plan_id>/preview")
def preview_plan(plan_id: int) -> Response:
    """
    What this plan would introduce, without committing to it.

    Cheap because the policy is pure, and it is the affordance that makes
    tweaking the knobs feel like an experiment rather than a decision.
    """
    con = _db()
    plan = store_plans.get(con, plan_id, user_id=_user_id())
    if plan is None:
        raise ApiError("unknown_plan", 404)
    body = _payload() if request.data else {}
    budget = int(body.get("budget") or 20)
    result = planned_policy.preview(
        con, plan, _day(body), budget=budget, course=_library().course.id, user_id=_user_id()
    )
    return jsonify(
        {
            "budget": budget,
            "picked": len(result.cards),
            "by_priority": result.by_priority,
            "unplanned": result.unplanned,
            "names": _preview_names(con, result.cards),
        }
    )


#: How many names the preview shows. Enough to recognise what a plan is about,
#: few enough that reading them is not a substitute for practising them.
PREVIEW_NAMES = 12


def _preview_names(
    con: sqlite3.Connection, card_ids: list[str], limit: int = PREVIEW_NAMES
) -> list[str]:
    """
    A shuffled handful of names for what this plan would introduce.

    A deliberate loosening of ADR-0005: a name is usually the answer, and the
    preview is one click from practising. Shuffling is a mitigation, not a fix
    -- it breaks the correlation between what you read here and what is served
    first, so the same few do not arrive in the order you just read them. You
    have still seen answers. Accepted knowingly, because the person reading the
    Design tab owns the material; the Study path is untouched and `public_card`
    remains the only serialiser of an open question.

    Card ids stay on this side of the wire, per ADR-0005 -- only names go out.
    """
    if not card_ids:
        return []
    # Sample before the query, not after: `budget` comes from the request, and a
    # plan asking for two thousand cards would otherwise put two thousand
    # placeholders into one statement, which SQLite refuses at 999.
    ids = list(card_ids)
    random.shuffle(ids)
    ids = ids[: max(limit * 4, 40)]
    marks = ",".join("?" for _ in ids)
    names = list(
        dict.fromkeys(
            r["label"]
            for r in con.execute(
                f"SELECT n.label AS label FROM cards c JOIN notes n ON n.id = c.note_id "
                f"WHERE c.id IN ({marks}) AND n.label IS NOT NULL AND n.label <> ''",
                ids,
            )
        )
    )
    # And again after, because rows come back in whatever order the join finds
    # them, which is stable and therefore not a shuffle.
    random.shuffle(names)
    return names[:limit]


@bp.get("/api/waiting")
def waiting() -> Response:
    """
    Everything outstanding, in one place.

    Four things were being recorded and none of them could be seen: staged
    changes only on the Manage tab, queued lesson notes only inside the composer
    that makes them, and flagged cards and filed issues nowhere at all -- their
    readers existed and no screen called them. Something you told the app and
    cannot find again is worse than something you could not tell it.

    Assembly only: every list here comes from the module that owns it.
    """
    con, course = _db(), _library().course.id
    changes = store_material.diff(con, course=course, user_id=_user_id())
    drafts = store_drafts.queued(con, course=course, user_id=_user_id())
    reports = store_reports.open_reports(con, course=course, user_id=_user_id())
    issues = store_issues.open_issues(con, course=course, user_id=_user_id())
    return jsonify(
        {
            "total": len(changes) + len(drafts) + len(reports) + len(issues),
            "changes": [
                {"what": d.label or d.note_id, "kind": d.kind, "note_id": d.note_id}
                for d in changes
            ],
            "drafts": [
                {"id": d.id, "summary": d.summary, "created_at": d.created_at} for d in drafts
            ],
            # The name, not the card id: ADR-0005 holds here as everywhere, and
            # a report is about an exercise the reader already knows by name.
            "reports": [
                {
                    "id": r.id,
                    "what": _named(con, r.note_id),
                    "reason": r.reason,
                    "note": r.note,
                    "unit": r.unit,
                    "at": r.reported_at,
                }
                for r in reports
            ],
            "issues": [
                {"id": i.id, "body": i.body, "about": i.selector, "at": i.raised_at} for i in issues
            ],
        }
    )


def _named(con: sqlite3.Connection, note_id: str) -> str:
    row = con.execute("SELECT label FROM notes WHERE id = ?", (note_id,)).fetchone()
    return (row["label"] if row and row["label"] else note_id) or note_id


@bp.get("/api/issues")
def list_issues() -> Response:
    issues = store_issues.open_issues(_db(), course=_library().course.id, user_id=_user_id())
    return jsonify(
        {
            "issues": [
                {
                    "id": i.id,
                    "kind": i.kind,
                    "body": i.body,
                    "selector": i.selector,
                    "raised_at": i.raised_at,
                }
                for i in issues
            ]
        }
    )


@bp.post("/api/issues")
def raise_issue() -> Response:
    body = _payload()
    try:
        issue = store_issues.raise_issue(
            _db(),
            body=str(body.get("body") or ""),
            kind=str(body.get("kind") or "other"),
            selector=body.get("selector") or None,
            course=_library().course.id,
            user_id=_user_id(),
        )
    except ValueError as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"id": issue.id, "kind": issue.kind})


@bp.post("/api/issues/<int:issue_id>/resolve")
def resolve_issue(issue_id: int) -> Response:
    body = _payload() if request.data else {}
    issue = store_issues.resolve(_db(), issue_id, note=body.get("note"), user_id=_user_id())
    if issue is None:
        raise ApiError("unknown_or_closed_issue", 404)
    return jsonify({"id": issue.id, "resolved_at": issue.resolved_at})


# --- managing the material -------------------------------------------------
#
# A deliberate carve-out from ADR-0005, and worth naming rather than leaving to
# be discovered. These endpoints return note ids and every field, answers
# included. They have to: you cannot fix a typo in an answer you cannot see.
#
# The rule that matters is untouched. ADR-0005 is about what reaches the client
# *while a question is open*, and nothing here changes that -- `public_card`
# remains the only path that serialises an open question, and the raw-bytes leak
# tests over `/api/session` and `/api/state` are not relaxed by a single byte.
# What this is, is the complement: `revealed` shows everything after an answer,
# and management shows everything when nothing has been asked at all. See
# ADR-0008.


def _reload_library() -> None:
    """
    Re-read the material after changing it, so the session serves the change.

    Unconditional since ADR-0015. It used to be skipped when no course directory
    was configured, which quietly meant that on a database-only deployment an
    edit was written and then not served until the next restart.

    Dropping rather than rebuilding: the next request that wants this course
    builds it. A write to course A must not pay to re-expand course B, and after
    a `save_set` on a 2415-note course the difference is felt.
    """
    _shelf().drop(_course())


def _label(note: Any, nt: Any, *, without: str | None = None) -> tuple[str, str]:
    """
    The question and the answer, as a person would read them.

    Composed from the note type's own `ask`/`expect` rather than from a list of
    field names kept here, so the board labels an exercise with the same fields
    the study path asks it with. `ask` is not a note field -- it is computed per
    card -- which is why the client cannot do this for itself.

    The language is not uniform and the caller should know it: for `gap` and
    `transform` the question is Portuguese, for `sentence` it is Polish and the
    Portuguese lives in the answer.
    """
    if nt is None or not nt.cards:
        return note.id, ""
    tpl = next(iter(nt.cards.values()))
    # `without` drops the field the family label already carries. For a `gap`
    # note the cue *is* the variant -- "morar — imperfeito, nós" -- so printing
    # it again on the row says the same thing twice in half the space.
    question = " · ".join(note.text(f) for f in tpl.ask if f != without and note.text(f))
    answer = " / ".join(note.answers(tpl.expect))
    return question or note.id, answer


def _note_json(
    note: Any,
    notetypes: dict[str, Any],
    *,
    state: str = "new",
    family: tuple[str, str] | None = None,
    family_field: str | None = None,
    facets: dict[str, list[str]] | None = None,
    edited_at: str | None = None,
) -> dict[str, Any]:
    nt = notetypes.get(note.notetype)
    problems = check(note, nt) if nt else []
    question, answer = _label(note, nt, without=family_field if family else None)
    return {
        # The short name the board shows. `question`/`answer` are the same
        # exercise read in full, and the row keeps them for hover and the
        # inspector -- a name identifies, a sentence explains, and a column of
        # sentences did neither.
        "label": note.label or question,
        "forms": {k: list(v) for k, v in note.forms.items()},
        "question": question,
        "answer": answer,
        "state": state,
        "family": family[0] if family else None,
        "variant": family[1] if family else None,
        "id": note.id,
        "notetype": note.notetype,
        "unit": note.unit,
        "ord": note.ord,
        "tags": list(note.tags),
        # The day this arrived. Set on 251 of 757 notes, forwarded by `create.js`
        # since the tab was written, and displayed by nothing -- so "what came in
        # on the 10th, and where did it go?" could only be answered by reading
        # the YAML (ADR-0013: lesson is one of the four concepts).
        "lesson": note.lesson.isoformat() if note.lesson else None,
        # Which shelf, which subject, which level -- the axes the board groups
        # and filters by. Derived from the tags, so this is the same taxonomy
        # Design plans against rather than a second one.
        "facets": facets or {},
        "fields": dict(note.fields),
        # Who wrote this, and whether anyone has changed it since. `origin` has
        # been on the wire since ADR-0010 named it as the field that says so,
        # and no screen read it; `edited_at` was not sent at all. Three states
        # out of two fields -- from a file, written here, changed here --
        # because "material an agent wrote" and "material I fixed afterwards"
        # are different things to know (ADR-0013 rule 3).
        "origin": note.origin,
        "edited_at": edited_at,
        # Split by severity, because the two mean different things to whoever is
        # editing. A fatal problem is a field giving away its own answer, and it
        # stops the exercise being served at all; a warning is advice. Showing
        # both as an alarm teaches people to ignore the alarm, and the fatal one
        # is the entire reason there is an alarm.
        "leaks": [str(p) for p in problems if p.fatal],
        "warnings": [str(p) for p in problems if not p.fatal],
    }


def _shape(nt: Any) -> dict[str, Any]:
    """
    A note type as the authoring screen needs it: its fields *and* its cards.

    `visible_before` is composed here rather than in the browser. It is not a
    list of fields marked "before" -- a card's `ask` fields are shown whatever
    their own visibility says, and its `expect` field never is -- and
    `NoteType.visible_before` is the one place that knows it. Recomposing it
    client-side is how a field ends up shown in one view and hidden in another.
    """
    return {
        "fields": {
            fname: {"type": spec.type, "required": spec.required, "visibility": spec.visibility}
            for fname, spec in nt.fields.items()
        },
        # Flask sorts the keys of everything it serialises, so a dict cannot
        # carry an order. These two can: `FIELD_ORDER` is the sequence a note
        # reads in when it is written to a file, and the authoring screen should
        # not invent a second one.
        "order": [n for n in FIELD_ORDER if n in nt.fields]
        + [n for n in nt.fields if n not in FIELD_ORDER],
        "card_order": list(nt.cards),
        "cards": {
            cname: {
                "ask": list(tpl.ask),
                "expect": tpl.expect,
                "grader": tpl.grader,
                "forms": list(tpl.forms),
                "requires": list(tpl.requires),
                "visible_before": list(nt.visible_before(cname)),
                # What this card could be asked as if its author chose. The
                # capability rules on top of this -- a word bank needs two words,
                # a choice needs wrong answers -- are per exercise, and come back
                # from `/api/material/check`.
                "askable": list(GRADER_FORMS.get(tpl.grader, SUPPORTED_FORMS)),
            }
            for cname, tpl in nt.cards.items()
        },
    }


@bp.get("/api/material")
def material() -> Response:
    """Every unit and every note in it, in full."""
    con, lib = _db(), _library()
    course = lib.course.id if lib.course else ""
    # Which sets this account studies, and which it may change. Two different
    # questions with two different answers: you can study somebody else's set
    # and not edit it, and you can own one you have chosen not to study.
    mine = store_users.studying(con, _user_id(), course)
    me = store_users.by_id(con, _user_id())
    units = [
        {
            "id": r["id"],
            "title": _json_or(r["title"], {}),
            "description": _json_or(r["description"], {}),
            "cefr": r["cefr"],
            "ord": r["ord"],
            "owner": r["owner"] or "",
            # `None` from `studying` means "has chosen nothing", which is the
            # whole course -- so every set reads as studied, which is true.
            "studying": True if mine is None else r["id"] in mine,
            "mine": bool(me and (not r["owner"] or r["owner"] == me.name or me.is_admin)),
        }
        for r in con.execute(
            "SELECT * FROM units WHERE course = ? AND archived_at IS NULL ORDER BY ord, id",
            (course,),
        )
    ]
    # One query for every note's state rather than one per note. A note has
    # several cards at different stages, and the badge shows the least advanced
    # of them -- "how well do I know this" answered conservatively.
    #
    # `s.user_id` in the join condition rather than the WHERE clause, and this is
    # the whole of the bug it fixes: without it the join produced a row per
    # account per card, and since the badge keeps the *least* advanced of them,
    # the board showed whichever of four people had got furthest behind. With it
    # in the WHERE clause instead, a card nobody has answered yet would drop out
    # of the LEFT JOIN entirely and never be counted as new. `catalogue.py:143`
    # has had it in the right place all along.
    worst: dict[str, str] = {}
    for row in con.execute(
        "SELECT c.note_id AS note_id, COALESCE(s.bucket, 'new') AS bucket "
        "FROM cards c "
        "JOIN notes n ON n.id = c.note_id "
        "LEFT JOIN card_state s ON s.card_id = c.id AND s.user_id = ? "
        "WHERE c.archived_at IS NULL AND n.course = ?",
        (_user_id(), course),
    ):
        bucket = row["bucket"] if row["bucket"] in BUCKET_ORDER else "new"
        best = worst.get(row["note_id"])
        if best is None or BUCKET_ORDER.index(bucket) < BUCKET_ORDER.index(best):
            worst[row["note_id"]] = bucket

    facets = store_cards.facets_from_db(con, course)

    # The axes the board can group and filter by. Four of them are fully or
    # largely populated on the live course and none was reachable from this tab
    # -- `/api/catalogue` has served them since Design was built, and Manage
    # grouped by set and nothing else (ADR-0013). One query, not one per note.
    touched_at = {
        r["id"]: r["edited_at"]
        for r in con.execute(
            "SELECT id, edited_at FROM notes WHERE edited_at IS NOT NULL AND course = ?",
            (course,),
        )
    }

    filed: dict[str, dict[str, list[str]]] = {}
    for row in con.execute(
        "SELECT f.note_id AS note_id, f.axis AS axis, f.value AS value "
        "FROM note_facets f JOIN notes n ON n.id = f.note_id WHERE n.course = ?",
        (course,),
    ):
        filed.setdefault(row["note_id"], {}).setdefault(row["axis"], []).append(row["value"])
    axes = [
        {"axis": a, "title": spec.title, "values": list(spec.values), "ordered": spec.ordered}
        for a, spec in facets.axes.items()
    ]

    notes = [
        _note_json(
            n,
            lib.notetypes,
            state=worst.get(n.id, "new"),
            family=family_of(n, facets),
            family_field=facets.family.field if facets.family else None,
            facets=filed.get(n.id, {}),
            edited_at=touched_at.get(n.id),
        )
        for n in store_material.live_notes(con, course or None)
    ]
    shapes = {name: _shape(nt) for name, nt in lib.notetypes.items()}
    return jsonify({"units": units, "notes": notes, "notetypes": shapes, "axes": axes})


@bp.post("/api/material/stage")
def stage_change() -> Response:
    body = _payload()
    try:
        change = store_material.stage(
            _db(),
            str(body.get("note_id") or ""),
            str(body.get("kind") or ""),
            body.get("payload"),
            user_id=_user_id(),
        )
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"note_id": change.note_id, "kind": change.kind})


@bp.get("/api/material/pending")
def pending_changes() -> Response:
    con = _db()
    return jsonify(
        {
            "changes": [
                {
                    "note_id": d.note_id,
                    "kind": d.kind,
                    "before": d.before,
                    "after": d.after,
                    "label": d.label,
                }
                for d in store_material.diff(con, course=_library().course.id, user_id=_user_id())
            ]
        }
    )


@bp.post("/api/material/confirm")
def confirm_changes() -> Response:
    con, lib = _db(), _library()
    report = store_material.apply_pending(
        con, lib.notetypes, course=lib.course.id, user_id=_user_id()
    )
    _reload_library()
    return jsonify(
        {
            "notes": report.notes,
            "sets": report.sets,
            # Both count operations on a set rather than on a note, so the note
            # count alone reports that nothing happened. `named` was added with
            # ADR-0013 and not serialised here, which is why naming a set
            # confirmed silently.
            "named": report.named,
            "restored": report.restored,
            "cards_added": report.cards_added,
            "cards_archived": report.cards_archived,
            # Applied, not refused -- the quarantine keeps these away from a
            # learner, and a half-finished edit needs somewhere to live. Named
            # so the change does not simply vanish from the course in silence.
            "quarantined": list(report.quarantined),
        }
    )


@bp.post("/api/material/discard")
def discard_changes() -> Response:
    body = _payload() if request.data else {}
    dropped = store_material.discard(
        _db(), body.get("note_id"), body.get("kind"), user_id=_user_id()
    )
    return jsonify({"discarded": dropped})


@bp.get("/api/export")
def export_bundle() -> Response:
    """
    The whole course as a zip, which is the only shape a browser can carry.

    The same bytes `repetita export` writes, through the same function, so a
    course downloaded here and one exported at a command line cannot come out
    differently. That matters more than it sounds: the promise in the README is
    that adding a lesson is a pull request a non-programmer can make, and it now
    rests entirely on this -- material written in the app exists nowhere else
    until it has been out through here.
    """
    from ..store.bundle import write_bundle

    course_id = current_app.config["REPETITA_COURSE_ID"]
    try:
        data = write_bundle(_db(), course_id)
    except LookupError as e:
        raise ApiError("unknown_course", 404) from e
    return current_app.response_class(
        data,
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{course_id}.zip"'},
    )


def _incoming(stack: ExitStack) -> tuple[Any, bool]:
    """
    The material an import request is asking about, from a zip or from disk.

    Two sources, one shape. An upload is the browser's route and the configured
    course directory is the command line's, and past this point nothing else in
    the request cares which it was.

    `stack` owns the temporary directory the zip is unpacked into: it has to
    outlive the loader, which reads the files lazily enough that unpacking into
    a directory that is already gone is a live mistake rather than a theoretical
    one.

    Returns the load and whether it was a whole course, which is what decides
    whether this import may archive anything.
    """
    from ..content.loader import load_course
    from ..store.bundle import BadBundle, read_bundle

    upload = request.files.get("bundle")
    if upload is not None:
        tmp = stack.enter_context(tempfile.TemporaryDirectory(prefix="repetita-import-"))
        try:
            root = read_bundle(upload.read(), tmp)
        except BadBundle as e:
            raise ApiError(str(e), 422) from e
        if not (root / "course.yaml").is_file():
            # Uploads are whole courses. A fragment needs a course named for it
            # and a rule about what it may not do, and neither is expressible in
            # a file picker -- `repetita import` is where that lives.
            raise ApiError("not_a_whole_course", 422)
        result = load_course(root)
        if result.course is None:
            raise ApiError("unreadable_course", 422)
        if result.course.id != current_app.config["REPETITA_COURSE_ID"]:
            # Merging one course into another would not fail: it would quietly
            # archive everything here and add everything there, and the ids
            # would collide on the way.
            raise ApiError("wrong_course", 409)
        return result, True

    course_dir = current_app.config.get("REPETITA_COURSE")
    if not course_dir:
        raise ApiError("no_course_configured", 409)
    result = load_course(course_dir)
    if result.course is None:
        raise ApiError("unreadable_course", 422)
    return result, True


def _preview_json(report: Any, clashes: list[Any]) -> dict[str, Any]:
    return {
        "added": report.added,
        "updated": report.updated,
        "archived": report.archived,
        # Named, not counted. Archiving is the part of an import that a person
        # has to be able to check against what they meant to do.
        "archived_ids": list(report.archived_ids),
        "restored": report.restored,
        "conflicts": [{"note_id": c.note_id, "file": c.file, "mine": c.mine} for c in clashes],
    }


@bp.post("/api/import/preview")
def import_preview() -> Response:
    """What importing would do, without doing any of it."""
    with ExitStack() as stack:
        result, whole = _incoming(stack)
        report, clashes = store_cards.preview_import(_db(), result, archive_missing=whole)
        return jsonify(_preview_json(report, clashes))


@bp.post("/api/import/apply")
def import_apply() -> Response:
    """
    Import, resolving each clash the way the learner said.

    A note not named keeps the version in the database. Defaulting the other way
    would mean an import silently destroyed work through omission -- the one
    outcome a confirmation step exists to make impossible.

    A snapshot is taken first, and named in the response. This is the only thing
    the app does that archives material in bulk, and CLAUDE.md's answer to an
    operation like that is to take a snapshot and then do it -- which is only
    useful if whoever needs it can find out what it was called.
    """
    from ..store import snapshots

    take_file = {str(n) for n in (request.form.getlist("take_file") or [])}
    if not take_file and request.is_json:
        body = _payload() if request.data else {}
        take_file = {str(n) for n in (body.get("take_file") or [])}

    with ExitStack() as stack:
        result, whole = _incoming(stack)

        # A snapshot that cannot be taken must not stop the import: the database
        # may be somewhere a copy will not fit, and refusing would make the app
        # unusable for a reason the learner cannot act on.
        saved = None
        with suppress(OSError):
            saved = snapshots.take(
                current_app.config["REPETITA_DB"], "before import", automatic=True
            )

        report = store_cards.sync(_db(), result, take_file=take_file, archive_missing=whole)

    _shelf().drop(_course())
    return jsonify(
        {
            "added": report.added,
            "updated": report.updated,
            "archived": report.archived,
            "archived_ids": list(report.archived_ids),
            "restored": report.restored,
            "kept_mine": list(report.conflicted),
            "snapshot": saved.name if saved else None,
        }
    )


@bp.post("/api/sets")
def create_set() -> Response:
    """A set that exists here before it exists in any course file."""
    body = _payload()
    lib = _library()
    course = lib.course.id if lib.course else ""
    try:
        unit = store_material.create_unit(
            _db(),
            course,
            str(body.get("id") or ""),
            title=body.get("title") or {},
            user_id=_user_id(),
        )
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    _reload_library()
    return jsonify({"id": unit})


@bp.post("/api/sets/<path:unit_id>/exercises")
def save_exercises(unit_id: str) -> Response:
    """
    Write a whole set: new exercises, edits, removals, in one go.

    Applied rather than staged, which is the one place this app departs from
    "nothing happens until Confirm". Writing an exercise is not editing one:
    there is no older version to be careful of, the Save button is the
    confirmation, and a set that had to be confirmed somewhere else would be
    half-made in two places at once.
    """
    body = _payload()
    con, lib = _db(), _library()
    course = lib.course.id if lib.course else ""
    try:
        report = store_material.save_set(
            con,
            course,
            unit_id,
            list(body.get("rows") or []),
            lib.notetypes,
            store_cards.facets_from_db(con, course),
            title=body.get("title"),
            user_id=_user_id(),
        )
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    _reload_library()
    return jsonify(
        {
            "created": report.created,
            "updated": report.updated,
            "archived": report.archived,
            "cards_added": report.cards_added,
            "cards_archived": report.cards_archived,
            # Said out loud: a staged edit to something just saved described a
            # version that no longer exists, and a later Confirm would have put
            # it back.
            "superseded": report.superseded,
            "quarantined": list(report.quarantined),
            "ids": list(report.ids),
        }
    )


def _choice_options(con: sqlite3.Connection, note: Any, nt: Any, template: str) -> list[str]:
    """
    The options a multiple choice would actually offer, for the preview.

    Composed the way `public_card` composes them -- the answer plus the two
    best-ranked wrong ones -- except unshuffled, because a preview that reordered
    itself on every keystroke would be unreadable and the order carries nothing
    either way.
    """
    accepted = note.answers(nt.cards[template].expect)
    if not accepted:
        return []
    wrong = [w for w in (note.fields.get("distractors") or []) if w]
    if note.id:
        wrong += [
            r["text"]
            for r in con.execute(
                "SELECT text FROM distractors WHERE card_id = ? ORDER BY rank LIMIT 4",
                (f"{note.id}#{template}",),
            )
        ]
    seen = {accepted[0]}
    kept = []
    for word in wrong:
        if word not in seen:
            seen.add(word)
            kept.append(word)
    return [accepted[0], *kept[: MIN_CHOICE_OPTIONS - 1]]


def _form_options(
    con: sqlite3.Connection, note: Any, nt: Any, template: str
) -> dict[str, str | None]:
    """
    Which forms this exercise could be asked in, and why not for the rest.

    `None` means available. A string is the reason, meant to be read on screen:
    an unavailable form that simply disappears teaches nobody anything, and the
    two capability rules -- a word bank needs two words, a multiple choice needs
    wrong answers -- are exactly what an author needs told.
    """
    tpl = nt.cards[template]
    tokens = len(answer_tokens(note, tpl.expect))
    wrong = len(note.fields.get("distractors") or [])
    if note.id:
        row = con.execute(
            "SELECT count(*) AS n FROM distractors WHERE card_id = ?", (f"{note.id}#{template}",)
        ).fetchone()
        wrong = max(wrong, row["n"] if row else 0)

    gradeable = GRADER_FORMS.get(tpl.grader, SUPPORTED_FORMS)
    out: dict[str, str | None] = {}
    for form in SUPPORTED_FORMS:
        if form not in gradeable:
            out[form] = f"the {tpl.grader} marking this exercise uses cannot judge a {form}"
        elif form == "wordbank" and tokens < MIN_WORDBANK_TOKENS:
            out[form] = "needs an answer of two words or more"
        elif form == "choice" and wrong < MIN_CHOICE_OPTIONS - 1:
            need = MIN_CHOICE_OPTIONS - 1
            out[form] = f"needs {need} wrong answers to choose between, has {wrong}"
        else:
            out[form] = None
    return out


@bp.post("/api/material/check")
def check_material() -> Response:
    """
    What would be wrong with these exercises, without writing any of them.

    The authoring screen asks this as you type. It is a route rather than a rule
    reimplemented in the browser on purpose: the leak test is accent-sensitive
    and word-boundary aware, and a second copy of it in JavaScript would be a
    second opinion about whether material is servable.
    """
    body = _payload()
    con, lib = _db(), _library()
    unit = str(body.get("unit") or "")
    # Once for the batch: the family rule is course configuration and does not
    # change between two rows of the same set.
    facets = store_cards.facets_from_db(con, lib.course.id if lib.course else "")
    out: list[dict[str, Any]] = []
    for position, row in enumerate(list(body.get("rows") or [])):
        nt = lib.notetypes.get(str(row.get("notetype") or ""))
        if nt is None:
            out.append({"refused": f"there is no {row.get('notetype')!r} exercise"})
            continue
        try:
            note = store_material.row_note(row, str(row.get("id") or ""), unit, position, nt)
        except store_material.NotEditable as e:
            out.append({"refused": str(e)})
            continue
        problems = check(note, nt)
        cards = [c.template for c in expand_cards(note, nt)]
        out.append(
            {
                "label": note.label or derive_label(note, nt, facets),
                "cards": cards,
                "forms": {t: _form_options(con, note, nt, t) for t in cards},
                "options": {t: _choice_options(con, note, nt, t) for t in cards},
                "leaks": [str(p) for p in problems if p.fatal],
                "warnings": [str(p) for p in problems if not p.fatal],
            }
        )
    return jsonify({"rows": out})


@bp.post("/api/sets/<path:unit_id>/remove")
def remove_set(unit_id: str) -> Response:
    """
    Stage the removal of a set. Nothing happens until Confirm.

    Staged rather than done, because this is the largest single thing the tab
    can do -- the set and every exercise in it -- and the drawer is where you
    find out how large before you agree to it.
    """
    try:
        store_material.stage(_db(), unit_id, "remove_set", True, user_id=_user_id())
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"staged": unit_id})


@bp.post("/api/sets/<path:unit_id>/restore")
def restore_set(unit_id: str) -> Response:
    """
    Stage bringing a set back. Nothing happens until Confirm.

    The other half of `remove`, and the half ADR-0006 has been owing since it
    chose *archived, never deleted*: the safety property is only real if there
    is a route back to the material, and until now there was none -- no screen
    could see an archived note and `restore` was a change kind nothing could
    stage.
    """
    try:
        store_material.stage(_db(), unit_id, "restore_set", True, user_id=_user_id())
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"staged": unit_id})


@bp.get("/api/material/archived")
def archived_material() -> Response:
    """
    What has left the course but not the database.

    A separate route rather than a flag on `/api/material`, because the board
    asks a different question of it: archived material has no bucket worth
    showing, no facets worth filtering, and nothing to drag. It is a list you
    read and restore from.
    """
    con, lib = _db(), _library()
    course = lib.course.id if lib.course else ""
    units = [
        {
            "id": r["id"],
            "title": _json_or(r["title"], {}),
            "description": _json_or(r["description"], {}),
            "archived_at": r["archived_at"],
        }
        for r in con.execute(
            "SELECT * FROM units WHERE course = ? AND archived_at IS NOT NULL ORDER BY id",
            (course,),
        )
    ]
    notes = [
        {
            "id": r["id"],
            "unit": r["unit"],
            "notetype": r["notetype"],
            "label": r["label"] or r["id"],
            "archived_at": r["archived_at"],
            "origin": r["origin"] or "",
            "lesson": r["lesson"],
        }
        for r in con.execute(
            "SELECT * FROM notes WHERE course = ? AND archived_at IS NOT NULL "
            "ORDER BY archived_at DESC, unit, ord",
            (course,),
        )
    ]
    return jsonify({"units": units, "notes": notes})


@bp.get("/api/drafts")
def list_drafts() -> Response:
    """What is queued, for the badge on the button and the list behind it."""
    return jsonify(
        {
            "drafts": [
                {
                    "id": d.id,
                    "summary": d.summary,
                    "created_at": d.created_at,
                    "processed_at": d.processed_at,
                    "outcome": d.outcome,
                }
                for d in store_drafts.all_drafts(_db(), user_id=_user_id())
            ]
        }
    )


@bp.post("/api/drafts")
def add_draft() -> Response:
    """
    Keep what was typed, exactly as typed.

    Nothing is parsed here on purpose -- see ADR-0009. A lesson is written down
    in one state of mind and turned into exercises in another, and a capture
    that argued with its input would be a capture nobody used.
    """
    body = _payload()
    text = str(body.get("body") or "").strip()
    if not text:
        raise ApiError("empty_draft", 400)
    draft = store_drafts.capture(_db(), text, course=_library().course.id, user_id=_user_id())
    return jsonify({"id": draft.id, "summary": draft.summary})


@bp.put("/api/sets/<path:unit_id>")
def rename_set(unit_id: str) -> Response:
    """
    Stage a set's name, description, or a new id.

    **Staged, not applied** -- ADR-0013. Removing a set already waited for
    Confirm and renaming one did not, so the two operations on a set's existence
    sat in different tabs under opposite commit models. They are one rule now,
    and this is the end of it that used to write straight through.

    Three weights of change. A title and a description move nothing; a new id is
    the directory the set exports to, and every note in it follows when Confirm
    applies it. Safe in a way a note id is not -- nothing in `card_state`
    references a unit.
    """
    body = _payload()
    payload = {
        "title": body.get("title"),
        "description": body.get("description"),
        "new_id": (body.get("new_id") or "").strip() or None,
    }
    try:
        change = store_material.stage(_db(), unit_id, "set_name", payload, user_id=_user_id())
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"staged": unit_id, "at": change.created_at})


# --- the admin page -------------------------------------------------------
#
# A fifth tab, and the only one gated on an account flag. Everything else here
# is reachable by anybody signed in, because these four people teach each other
# (ADR-0008's amendment) -- this is different: it reads every table in the
# database, including four accounts' study history, and a person's review log is
# theirs even among people who share their material.


def _must_be_admin() -> None:
    """
    The gate, as one function called by every route below.

    A 403 rather than a 404: hiding the admin page from a signed-in account that
    cannot use it would be pretending it does not exist to people who can see
    the tab is missing and ask why. They are four people who know each other.
    """
    if not current_user().is_admin:
        raise ApiError("not_an_admin", 403)


def _table_json(table: store_browse.Table) -> dict[str, Any]:
    return {
        "name": table.name,
        "columns": list(table.columns),
        "primary_key": list(table.primary_key),
        "rows": table.rows,
        "editable": table.editable,
        # Why not, and where to go instead. Shown on the page rather than kept
        # in the source: a read-only field with no explanation reads as a bug.
        "frozen": table.frozen,
        "instead": table.instead,
        # Rows inside an editable table that are not, as {column, values}.
        "locked": (
            {"column": table.locked[0], "values": list(table.locked[1])} if table.locked else None
        ),
    }


@bp.get("/api/admin/tables")
def admin_tables() -> Response:
    """Every table, its size, and whether this page may write to it."""
    _must_be_admin()
    return jsonify({"tables": [_table_json(t) for t in store_browse.tables(_db())]})


@bp.get("/api/admin/tables/<name>")
def admin_rows(name: str) -> Response:
    """One page of one table, optionally narrowed by exact column matches."""
    _must_be_admin()
    where = {
        k[2:]: v
        for k, v in request.args.items()
        # `f.` prefixes a filter, so `limit` and `offset` cannot collide with a
        # column of the same name -- and `notes` has neither, which is exactly
        # the kind of thing that stops being true later.
        if k.startswith("f.")
    }
    try:
        page = store_browse.read(
            _db(),
            name,
            offset=int(request.args.get("offset") or 0),
            limit=int(request.args.get("limit") or store_browse.PAGE),
            where=where,
        )
    except LookupError:
        raise ApiError("unknown_table", 404) from None
    except ValueError:
        raise ApiError("bad_paging", 400) from None
    return jsonify(
        {
            "table": _table_json(page.table),
            "rows": page.rows,
            "offset": page.offset,
            "total": page.total,
        }
    )


@bp.patch("/api/admin/tables/<name>")
def admin_update(name: str) -> Response:
    """Change one row, addressed by its whole primary key."""
    _must_be_admin()
    body = _payload()
    try:
        changed = store_browse.update(
            _db(), name, dict(body.get("key") or {}), dict(body.get("values") or {})
        )
    except LookupError:
        raise ApiError("unknown_table", 404) from None
    except store_browse.Frozen as e:
        raise ApiError(str(e), 400) from None
    except sqlite3.Error as e:
        # A constraint the schema is enforcing -- a unique name, a NOT NULL.
        # Reported rather than turned into a 500: it is a fact about the row
        # somebody typed, not a failure of this endpoint.
        raise ApiError(str(e), 400) from None
    return jsonify({"changed": changed})


@bp.delete("/api/admin/tables/<name>")
def admin_delete(name: str) -> Response:
    """Remove one row, addressed by its whole primary key."""
    _must_be_admin()
    body = _payload()
    try:
        gone = store_browse.delete(_db(), name, dict(body.get("key") or {}))
    except LookupError:
        raise ApiError("unknown_table", 404) from None
    except store_browse.Frozen as e:
        raise ApiError(str(e), 400) from None
    except sqlite3.Error as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"deleted": gone})


@bp.get("/api/admin/accounts")
def admin_accounts() -> Response:
    """
    The accounts, with what each one is signed up for.

    A form of its own rather than the generic table editor, because `users` has
    a column the generic editor must never show or write: `password_hash`. It
    is not in this response and there is no route that returns it.
    """
    _must_be_admin()
    con = _db()
    return jsonify(
        {
            "accounts": [
                {
                    "id": u.id,
                    "name": u.name,
                    "display": u.display,
                    "admin": u.is_admin,
                    "active": u.active,
                    "has_password": u.has_password,
                    "courses": store_users.enrolments(con, u.id),
                }
                for u in store_users.everyone(con, include_inactive=True)
            ],
            "courses": store_cards.courses_in_db(con),
        }
    )


@bp.post("/api/admin/accounts")
def admin_account_change() -> Response:
    """
    Add an account, set a password, rename, activate, enrol.

    One route with a verb rather than six, because the shape of each is the same
    and the page is one form. Every one of them goes through `store.users`,
    which is where hashing, the `units.owner` carry on rename, and "deactivate
    rather than delete" live -- none of that is re-implemented here.
    """
    _must_be_admin()
    body = _payload()
    verb = str(body.get("verb") or "")
    con = _db()
    try:
        if verb == "add":
            store_users.add(
                con,
                str(body.get("name") or ""),
                password=str(body.get("password") or ""),
                display=str(body.get("display") or ""),
                is_admin=bool(body.get("admin")),
            )
        elif verb == "passwd":
            store_users.set_password(con, str(body.get("name")), str(body.get("password") or ""))
        elif verb == "rename":
            store_users.rename(con, str(body.get("name")), str(body.get("to") or ""))
        elif verb == "active":
            store_users.set_active(con, str(body.get("name")), bool(body.get("active")))
        elif verb in ("enrol", "leave"):
            act = store_users.enrol if verb == "enrol" else store_users.unenrol
            act(con, str(body.get("name")), str(body.get("course") or ""))
        else:
            raise ApiError("unknown_verb", 400)
        return jsonify({"ok": True})
    except store_users.UnknownUser:
        raise ApiError("unknown_account", 404) from None
    except (store_users.NameTaken, ValueError) as e:
        raise ApiError(str(e), 400) from None


@bp.post("/api/admin/own")
def admin_own() -> Response:
    """
    Say whose a set is.

    The one content-ish thing an admin genuinely needs to change, and it gets a
    route rather than falling under the generic editor -- `units` is read-only
    there because a write to it owes four other things. This writes one column
    that owes nothing, through the function that owns it.
    """
    _must_be_admin()
    body = _payload()
    course = str(body.get("course") or _course())
    unit = str(body.get("set") or "")
    owner = str(body.get("owner") or "")
    if not unit:
        raise ApiError("name_a_set", 400)
    if owner and store_users.by_name(_db(), owner) is None:
        raise ApiError("unknown_account", 404)
    if not store_material.set_owner(_db(), course, unit, owner):
        raise ApiError("unknown_set", 404)
    return jsonify({"set": unit, "owner": owner})


@bp.post("/api/sets/<path:unit_id>/study")
def study_set(unit_id: str) -> Response:
    """
    Add a set to what this account studies, or take it away.

    Joining takes no permission. Reading somebody else's material is never
    restricted here (ADR-0008's amendment) -- you can already see every word of
    it in Manage, and studying it is reading it. Ownership governs *changing* a
    set, which is `material.may_edit`, and is a different question.

    Leaving removes one row and nothing else: every answer and every schedule
    for those cards stays where it is, so rejoining is rejoining rather than
    starting again (rule 1).
    """
    con, course = _db(), _course()
    body = _payload()
    found = con.execute(
        "SELECT 1 FROM units WHERE course = ? AND id = ?", (course, unit_id)
    ).fetchone()
    if found is None:
        raise ApiError("unknown_set", 404)

    # Writing down every set on the first choice is `set_studying`'s job, not
    # this route's: the rule belongs beside the table it is about, where the CLI
    # and the admin page get it too.
    wanted = bool(body.get("studying", True))
    store_users.set_studying(con, _user_id(), course, unit_id, wanted)
    picked = store_users.studying(con, _user_id(), course)
    return jsonify({"set": unit_id, "studying": wanted, "sets": list(picked) if picked else None})
