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

from .. import graders, srs
from ..content.models import Course
from ..core.protocols import GradingOptions
from ..core.types import Response as Answer
from ..policies import daily
from ..store import cards as store_cards
from ..store import db as store_db
from ..store import reviews
from .serialize import public_card, revealed, served_form

if TYPE_CHECKING:  # `app` imports this module, so the real import would cycle.
    from .app import Library

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
            "quarantined": lib.quarantined,
        }
    )


@bp.get("/api/session")
def session() -> Response:
    """Today's queue, every card serialised with its question open."""
    con, lib, today = _db(), _library(), _day()
    plan = daily.build_session(con, today)
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
        cards.append(
            public_card(
                card,
                note,
                notetype,
                handle=lib.handles.handle(card_id),
                rng=rng,
                state=states.get(card_id),
            )
        )

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
    )

    return jsonify(
        {
            "card_id": card.id,
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
