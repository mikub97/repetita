"""SQLite persistence: content cache, card state, and the append-only review log."""

from .cards import (
    CardState,
    all_states,
    card_ids,
    declare_known,
    declared_count,
    distractor_counts,
    distractors_for,
    get_state,
    save_state,
    sync,
    undo_known,
)
from .db import connect, session
from .reviews import (
    count_on,
    first_seen_on,
    lesson_first_seen_on,
    recent_ratings,
    record_answer,
)

__all__ = [
    "CardState",
    "all_states",
    "card_ids",
    "connect",
    "count_on",
    "declare_known",
    "declared_count",
    "distractor_counts",
    "distractors_for",
    "first_seen_on",
    "get_state",
    "lesson_first_seen_on",
    "recent_ratings",
    "recent_ratings_for",
    "record_answer",
    "save_state",
    "session",
    "sync",
    "undo_known",
]
