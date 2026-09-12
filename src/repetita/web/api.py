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
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request

from .. import graders, policies, srs
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
from ..policies import daily
from ..policies import planned as planned_policy
from ..store import cards as store_cards
from ..store import catalogue as store_catalogue
from ..store import db as store_db
from ..store import drafts as store_drafts
from ..store import issues as store_issues
from ..store import material as store_material
from ..store import plans as store_plans
from ..store import reports as store_reports
from ..store import reviews
from .serialize import (
    MIN_CHOICE_OPTIONS,
    MIN_WORDBANK_TOKENS,
    SUPPORTED_FORMS,
    answer_tokens,
    public_card,
    revealed,
    served_form,
)

if TYPE_CHECKING:  # `app` imports this module, so the real import would cycle.
    from .app import Library

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


def _db() -> sqlite3.Connection:
    con: sqlite3.Connection | None = g.get("repetita_db")
    if con is None:
        con = store_db.connect(current_app.config["REPETITA_DB"])
        g.repetita_db = con
    return con


def _library() -> Library:
    library: Library = current_app.extensions["repetita"]
    return library


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
        plan = store_plans.get(con, int(raw))
    except (TypeError, ValueError):
        raise ApiError("plan must be an id", 400) from None
    if plan is None:
        # A well-formed id for a plan that is not there. Falling back to the
        # plain session would answer a question nobody asked, and would file the
        # answers with no revision at all -- so the one endpoint that can tell
        # you the plan is gone would be the one that says nothing.
        raise ApiError("unknown_plan", 404)
    return plan


def _revision_for(con: sqlite3.Connection, body: dict[str, Any]) -> int | None:
    """
    Which revision served this answer, resolved here rather than trusted.

    The client says *which plan* it was practising; the server decides which
    revision that is. Same reason `form` is recomputed on the way in: the log is
    a record of what happened, and a client is free to lie.
    """
    plan = _requested_plan(con, body)
    return store_plans.latest_revision(con, plan.id) if plan else None


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


@bp.get("/api/state")
def state() -> Response:
    """Counters, and nothing that could answer a question."""
    con, lib, today = _db(), _library(), _day()
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
            "owed": daily.owed_count(con, today),
            "answered_today": reviews.count_on(con, today),
            "target": daily.DAILY_TARGET,
            "done": daily.day_done(con, today),
            "gate_open": daily.gate_open(reviews.recent_ratings(con, daily.GATE_WINDOW)),
            "cards": len(lib.cards),
            # Cards taken out on the learner's word rather than on evidence.
            # Surfaced because a claim nobody can see is a claim nobody can
            # revisit, and a mis-click would otherwise be invisible forever.
            "declared": store_cards.declared_count(con),
            # Exercises reported broken and not yet dealt with. Same argument as
            # `declared`: a report nobody can see is a report nobody acts on.
            "reports_open": store_reports.open_report_count(con),
            "quarantined": lib.quarantined,
        }
    )


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
    plan = policy.build(con, today, plan=study_plan)
    rng = random.Random()
    # One read for the whole queue rather than one per card: the presenter needs
    # each card's history to decide how to ask it.
    states = store_cards.all_states(con)

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
    from .app import build_library

    before = _library()
    try:
        library = build_library(
            current_app.config["REPETITA_DB"], current_app.config["REPETITA_COURSE_ID"]
        )
    except (ValueError, LookupError) as broken:
        raise ApiError(str(broken), 422) from broken

    current_app.extensions["repetita"] = library
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
        store_cards.undo_known(con, card.id)
        if undo
        else store_cards.declare_known(con, card.id, today, backend=srs.get(lib.course.scheduler))
    )

    return jsonify(
        {
            "declared": state.retired_reason == store_cards.DECLARED if state else False,
            "reason": state.retired_reason if state else None,
            "owed": daily.owed_count(con, today),
            "declared_total": store_cards.declared_count(con),
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
        store_reports.withdraw_report(con, card.id)
        return jsonify(
            {
                "reported": False,
                "owed": daily.owed_count(con, today),
                "reports_open": store_reports.open_report_count(con),
            }
        )

    reason = str(body.get("reason") or "")
    if reason not in store_reports.REASONS:
        raise ApiError("bad_reason", 400)

    note = lib.notes[card.note_id]
    notetype = lib.notetypes[card.notetype]
    state = store_cards.get_state(con, card.id)
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
            form=served_form(card, note, notetype, state=state, distractors=options),
            fields=dict(note.fields),
            origin=note.origin,
            unit=note.unit,
        ),
    )

    return jsonify(
        {
            "reported": True,
            "owed": daily.owed_count(con, today),
            "reports_open": store_reports.open_report_count(con),
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
    before = store_cards.get_state(con, card.id)

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
        form=served_form(card, note, notetype, state=before),
        # Wrong answers too. In a year these are the best distractors available,
        # because they are the mistakes real learners made.
        answer=given.text or given.choice,
        duration_ms=given.ms,
        # Which revision of which plan chose to serve this card. Recorded now
        # because it cannot be reconstructed later -- ADR-0003.
        plan_revision_id=_revision_for(con, body),
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
            "owed": daily.owed_count(con, today),
            "answered_today": reviews.count_on(con, today),
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
    con = _db()
    group_by = [
        d.strip() for d in (request.args.get("group_by") or "topic").split(",") if d.strip()
    ]
    rows = store_catalogue.catalogue(
        con, group_by=group_by, where=_selector(request.args.get("where"))
    )

    axes = [
        {
            "axis": r["axis"],
            "title": _json_or(r["title"], {}),
            "ordered": bool(r["ordered"]),
            "catch_all": bool(r["catch_all"]),
        }
        for r in con.execute("SELECT * FROM facet_axes ORDER BY ord")
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
            for value, m in store_catalogue.mastery_by(con, group_by[0]).items()
        }

    return jsonify(
        {
            "group_by": group_by,
            "axes": axes,
            "mastery": mastery,
            "rows": [{**r.keys, "cards": r.cards, "notes": r.notes} for r in rows],
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
    return jsonify({"plans": [_plan_json(p) for p in store_plans.all_plans(_db())]})


@bp.post("/api/plans")
def create_plan() -> Response:
    body = _payload()
    name = str(body.get("name") or "").strip()
    if not name:
        raise ApiError("a plan needs a name", 400)
    lib = _library()
    course = lib.course.id if lib.course else ""
    plan = store_plans.create(_db(), name, course, active=bool(body.get("active")))
    return jsonify(_plan_json(plan))


@bp.put("/api/plans/<int:plan_id>")
def update_plan(plan_id: int) -> Response:
    body = _payload()
    con = _db()
    if store_plans.get(con, plan_id) is None:
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
        store_plans.set_priorities(con, plan_id, priorities)

    if "knobs" in body:
        try:
            store_plans.set_knobs(con, plan_id, dict(body["knobs"] or {}))
        except (TypeError, ValueError) as e:
            raise ApiError(str(e), 400) from None

    if body.get("active"):
        store_plans.activate(con, plan_id)

    plan = store_plans.get(con, plan_id)
    assert plan is not None
    return jsonify(_plan_json(plan))


@bp.delete("/api/plans/<int:plan_id>")
def delete_plan(plan_id: int) -> Response:
    store_plans.delete(_db(), plan_id)
    return jsonify({"deleted": plan_id})


@bp.post("/api/plans/<int:plan_id>/preview")
def preview_plan(plan_id: int) -> Response:
    """
    What this plan would introduce, without committing to it.

    Cheap because the policy is pure, and it is the affordance that makes
    tweaking the knobs feel like an experiment rather than a decision.
    """
    con = _db()
    plan = store_plans.get(con, plan_id)
    if plan is None:
        raise ApiError("unknown_plan", 404)
    body = _payload() if request.data else {}
    budget = int(body.get("budget") or 20)
    result = planned_policy.preview(con, plan, _day(body), budget=budget)
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
    con = _db()
    changes = store_material.diff(con)
    drafts = store_drafts.queued(con)
    reports = store_reports.open_reports(con)
    issues = store_issues.open_issues(con)
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
    issues = store_issues.open_issues(_db())
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
        )
    except ValueError as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"id": issue.id, "kind": issue.kind})


@bp.post("/api/issues/<int:issue_id>/resolve")
def resolve_issue(issue_id: int) -> Response:
    body = _payload() if request.data else {}
    issue = store_issues.resolve(_db(), issue_id, note=body.get("note"))
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
    """
    from .app import build_library

    current_app.extensions["repetita"] = build_library(
        current_app.config["REPETITA_DB"], current_app.config["REPETITA_COURSE_ID"]
    )


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
    units = [
        {
            "id": r["id"],
            "title": _json_or(r["title"], {}),
            "description": _json_or(r["description"], {}),
            "cefr": r["cefr"],
            "ord": r["ord"],
        }
        for r in con.execute(
            "SELECT * FROM units WHERE course = ? AND archived_at IS NULL ORDER BY ord, id",
            (course,),
        )
    ]
    # One query for every note's state rather than one per note. A note has
    # several cards at different stages, and the badge shows the least advanced
    # of them -- "how well do I know this" answered conservatively.
    worst: dict[str, str] = {}
    for row in con.execute(
        "SELECT c.note_id AS note_id, COALESCE(s.bucket, 'new') AS bucket "
        "FROM cards c LEFT JOIN card_state s ON s.card_id = c.id "
        "WHERE c.archived_at IS NULL"
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
        for r in con.execute("SELECT id, edited_at FROM notes WHERE edited_at IS NOT NULL")
    }

    filed: dict[str, dict[str, list[str]]] = {}
    for row in con.execute("SELECT note_id, axis, value FROM note_facets"):
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
                for d in store_material.diff(con)
            ]
        }
    )


@bp.post("/api/material/confirm")
def confirm_changes() -> Response:
    con, lib = _db(), _library()
    report = store_material.apply_pending(con, lib.notetypes)
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
    dropped = store_material.discard(_db(), body.get("note_id"), body.get("kind"))
    return jsonify({"discarded": dropped})


@bp.post("/api/import/preview")
def import_preview() -> Response:
    """What re-reading the course files would do, without doing any of it."""
    from ..content.loader import load_course

    course_dir = current_app.config.get("REPETITA_COURSE")
    if not course_dir:
        raise ApiError("no_course_configured", 409)
    result = load_course(course_dir)
    if result.course is None:
        raise ApiError("unreadable_course", 422)
    report, clashes = store_cards.preview_import(_db(), result)
    return jsonify(
        {
            "added": report.added,
            "updated": report.updated,
            "archived": report.archived,
            "restored": report.restored,
            "conflicts": [{"note_id": c.note_id, "file": c.file, "mine": c.mine} for c in clashes],
        }
    )


@bp.post("/api/import/apply")
def import_apply() -> Response:
    """
    Import, resolving each clash the way the learner said.

    A note not named keeps the version in the database. Defaulting the other way
    would mean an import silently destroyed work through omission -- the one
    outcome a confirmation step exists to make impossible.
    """
    from .app import build_library

    body = _payload() if request.data else {}
    take_file = {str(n) for n in (body.get("take_file") or [])}
    course_dir = current_app.config.get("REPETITA_COURSE")
    if not course_dir:
        raise ApiError("no_course_configured", 409)

    from ..content.loader import load_course

    result = load_course(course_dir)
    if result.course is None:
        raise ApiError("unreadable_course", 422)
    report = store_cards.sync(_db(), result, take_file=take_file)
    current_app.extensions["repetita"] = build_library(
        current_app.config["REPETITA_DB"], current_app.config["REPETITA_COURSE_ID"]
    )
    return jsonify(
        {
            "added": report.added,
            "updated": report.updated,
            "archived": report.archived,
            "kept_mine": list(report.conflicted),
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
            _db(), course, str(body.get("id") or ""), title=body.get("title") or {}
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
        store_material.stage(_db(), unit_id, "remove_set", True)
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
        store_material.stage(_db(), unit_id, "restore_set", True)
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
                for d in store_drafts.all_drafts(_db())
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
    draft = store_drafts.capture(_db(), text)
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
        change = store_material.stage(_db(), unit_id, "set_name", payload)
    except store_material.NotEditable as e:
        raise ApiError(str(e), 400) from None
    return jsonify({"staged": unit_id, "at": change.created_at})
