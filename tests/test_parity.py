"""
The rewrite did not damage a real learner's schedule.

Same content, same history, same day -> same queue as the predecessor. That is
the only claim that can be checked against a year of real use, and it is the
reason this file exists.

Two things make a parity test like this easy to get wrong, and both are avoided
deliberately.

**The predecessor is transcribed, not imported.** `_Legacy` below is a copy of
`hub/pt/srs.py` and `hub/pt/store.py::build_session` as they stood at
`roda-2026-09-06-pre-refactor`, reduced to what building a queue needs. The
predecessor is a private application and cannot be a test dependency, so a copy
is the only option -- and a copy that drifts is worse than none, which is why
every constant here is asserted against the engine's own value in
`TestTheTranscriptionIsFaithful`. If someone retunes `NEW_EVERY` and this file
stops describing the predecessor, that test fails first and says so.

**Every divergence is asserted, not excluded.** The new engine differs from the
old one in known ways -- sibling burying (ADR-0001), HARD as a pass (ADR-0002),
and three smaller things found while writing this file. Each has a test that
pins the difference and says why it is intended. A parity test that passes by
being vague is worse than no parity test, so nothing here is skipped, softened,
or compared as an unordered set where order is the thing at issue.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import sqlite3
from pathlib import Path
from typing import ClassVar

import pytest

from repetita import store
from repetita.content.notetypes import get as notetype_of
from repetita.core.types import Rating
from repetita.importers import hub
from repetita.policies import daily
from repetita.srs import sm2

TODAY = dt.date(2026, 9, 6)

# The predecessor's schema, verbatim. Only the tables the queue reads.
LEGACY_SCHEMA = """
CREATE TABLE items (
  key TEXT PRIMARY KEY, pack TEXT NOT NULL, track TEXT NOT NULL,
  topic TEXT NOT NULL, level TEXT NOT NULL, type TEXT NOT NULL,
  ord INTEGER NOT NULL, srs INTEGER NOT NULL DEFAULT 1,
  payload TEXT NOT NULL, lesson TEXT);
CREATE TABLE progress (
  key TEXT PRIMARY KEY, ease REAL NOT NULL, interval INTEGER NOT NULL,
  reps INTEGER NOT NULL, lapses INTEGER NOT NULL, due TEXT, last TEXT,
  seen INTEGER NOT NULL, correct INTEGER NOT NULL, wrong INTEGER NOT NULL,
  retired_at TEXT, retired_reason TEXT, suspended_at TEXT);
CREATE TABLE reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, at TEXT NOT NULL,
  day TEXT NOT NULL, grade INTEGER NOT NULL, answer TEXT, ms INTEGER,
  scheduled INTEGER NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'sessao');
CREATE TABLE pack_prefs (
  pack TEXT PRIMARY KEY, rank INTEGER NOT NULL DEFAULT 0, paused_at TEXT);
"""


# --------------------------------------------------------------------------
# The predecessor, transcribed.
# --------------------------------------------------------------------------


class _Legacy:
    """`hub/pt/srs.py` + `build_session`, reduced to the queue."""

    GATE_WINDOW = 20
    GATE_THRESHOLD = 0.75
    GATE_MIN_ANSWERS = 8
    LESSON_FRESH_DAYS = 3
    LESSON_INTRO_CAP = 12
    NEW_EVERY = 3
    PASS_THRESHOLD = 3
    MATURE_DAYS = 21
    NEW_SPLIT: ClassVar[dict[str, float]] = {
        "vocabulario": 0.5,
        "gramatica": 0.3,
        "musica": 0.1,
        "fala": 0.1,
    }

    @staticmethod
    def is_due(rec: sqlite3.Row | None, today: dt.date) -> bool:
        if rec is None or rec["seen"] == 0 or rec["retired_at"] or rec["suspended_at"]:
            return False
        return rec["due"] is not None and rec["due"] <= today.isoformat()

    @classmethod
    def status_is_weak(cls, rec: sqlite3.Row) -> bool:
        """The predecessor's consolidation pool: not new, not mastered, not gone."""
        if rec["seen"] == 0 or rec["retired_at"] or rec["suspended_at"]:
            return False
        return rec["interval"] < cls.MATURE_DAYS

    @classmethod
    def gate_open(cls, grades: list[int]) -> bool:
        if len(grades) < cls.GATE_MIN_ANSWERS:
            return True
        recent = grades[: cls.GATE_WINDOW]
        return sum(1 for g in recent if g >= cls.PASS_THRESHOLD) / len(recent) >= cls.GATE_THRESHOLD

    @classmethod
    def lesson_is_fresh(cls, lesson: str | None, today: dt.date) -> bool:
        if not lesson:
            return False
        try:
            return (today - dt.date.fromisoformat(lesson)).days <= cls.LESSON_FRESH_DAYS
        except ValueError:
            return False

    @staticmethod
    def _invert_date(iso: str) -> str:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{9999 - y:04d}-{12 - m:02d}-{31 - d:02d}"

    @classmethod
    def _interleave_tracks(cls, rows: list[sqlite3.Row]) -> list[str]:
        by_track: dict[str, list[str]] = {}
        for r in rows:
            by_track.setdefault(r["track"], []).append(r["key"])
        order = sorted(by_track, key=lambda tr: -cls.NEW_SPLIT.get(tr, 0))
        out: list[str] = []
        while any(by_track.values()):
            for tr in order:
                if by_track.get(tr):
                    out.append(by_track[tr].pop(0))
        return out

    @classmethod
    def introduction_order(cls, con: sqlite3.Connection, today: dt.date) -> list[str]:
        rows = list(
            con.execute("SELECT key, track, topic, pack, ord, lesson FROM items WHERE srs=1")
        )
        records = {r["key"]: r for r in con.execute("SELECT * FROM progress")}
        prefs = {
            r["pack"]: {"rank": r["rank"], "paused": r["paused_at"] is not None}
            for r in con.execute("SELECT pack, rank, paused_at FROM pack_prefs")
        }

        def eligible(r: sqlite3.Row) -> bool:
            rec = records.get(r["key"])
            if rec is not None and (rec["seen"] > 0 or rec["suspended_at"]):
                return False
            if rec is not None and rec["due"] and rec["due"] > today.isoformat():
                return False
            return not prefs.get(r["pack"], {}).get("paused", False)

        def group_key(r: sqlite3.Row) -> tuple[int, int, str]:
            rank = int(prefs.get(r["pack"], {}).get("rank", 0) or 0)
            lesson = r["lesson"]
            return (-rank, 0 if lesson else 1, "" if not lesson else cls._invert_date(lesson))

        pool = sorted(
            (r for r in rows if eligible(r)),
            key=lambda r: (group_key(r), r["pack"], r["ord"]),
        )
        out: list[str] = []
        i = 0
        while i < len(pool):
            j = i
            while j < len(pool) and group_key(pool[j]) == group_key(pool[i]):
                j += 1
            out.extend(cls._interleave_tracks(pool[i:j]))
            i = j
        return out

    @classmethod
    def lesson_intro_used(cls, con: sqlite3.Connection, today: dt.date) -> int:
        cutoff = (today - dt.timedelta(days=cls.LESSON_FRESH_DAYS)).isoformat()
        row = con.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT r.key, MIN(r.day) first_day FROM reviews r"
            "  JOIN items i ON i.key = r.key"
            "  WHERE i.lesson IS NOT NULL AND i.lesson >= ?"
            "  GROUP BY r.key HAVING first_day = ?)",
            (cutoff, today.isoformat()),
        ).fetchone()
        return int(row[0] or 0)

    @classmethod
    def gated_introductions(
        cls, con: sqlite3.Connection, today: dt.date, ordered: list[str]
    ) -> list[str]:
        grades = [
            r["grade"] for r in con.execute("SELECT grade FROM reviews ORDER BY id DESC LIMIT 40")
        ]
        if cls.gate_open(grades):
            return ordered
        lessons = {r["key"]: r["lesson"] for r in con.execute("SELECT key, lesson FROM items")}
        budget = cls.LESSON_INTRO_CAP - cls.lesson_intro_used(con, today)
        if budget <= 0:
            return []
        return [k for k in ordered if cls.lesson_is_fresh(lessons.get(k), today)][:budget]

    @staticmethod
    def weave(due: list[str], new: list[str], every: int) -> list[str]:
        out: list[str] = []
        di = ni = 0
        while di < len(due) or ni < len(new):
            taken = 0
            while di < len(due) and taken < every:
                out.append(due[di])
                di += 1
                taken += 1
            if ni < len(new):
                out.append(new[ni])
                ni += 1
            elif di >= len(due):
                break
        return out

    @classmethod
    def build_session(
        cls, con: sqlite3.Connection, today: dt.date, limit: int = 40
    ) -> tuple[list[str], bool]:
        tracks = {r["key"]: r["track"] for r in con.execute("SELECT key, track FROM items")}
        records = {r["key"]: r for r in con.execute("SELECT * FROM progress")}

        due = [k for k in tracks if cls.is_due(records.get(k), today)]
        due.sort(key=lambda k: records[k]["due"] or "")

        picked = cls.gated_introductions(con, today, cls.introduction_order(con, today))

        consolidation: list[str] = []
        if not due and not picked:
            weak = [(k, r) for k, r in records.items() if k in tracks and cls.status_is_weak(r)]
            weak.sort(key=lambda kr: (-kr[1]["lapses"], kr[1]["ease"], kr[1]["interval"]))
            consolidation = [k for k, _ in weak]

        queue = cls.weave(due, picked, cls.NEW_EVERY) + consolidation
        return queue[:limit], len(queue) > limit


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class HubBuilder:
    """A predecessor database, built one exercise at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(LEGACY_SCHEMA)
        self._ord: dict[str, int] = {}
        self._clock = 0

    def item(
        self,
        key: str,
        *,
        type: str = "gap",
        pack: str | None = None,
        track: str = "gramatica",
        lesson: str | None = None,
        srs: int = 1,
        payload: dict[str, object] | None = None,
    ) -> str:
        pack = pack or key.split(".")[0]
        ordinal = self._ord.get(pack, 0)
        self._ord[pack] = ordinal + 1
        body: dict[str, object] = payload or {
            "id": key.split(".")[-1],
            "type": type,
            "prompt": f"Eu ___ {key}.",
            "answers": [f"resposta-{key}"],
            "cue": f"cue {key}",
        }
        self.con.execute(
            "INSERT INTO items(key,pack,track,topic,level,type,ord,srs,payload,lesson) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, pack, track, pack, "A2", type, ordinal, srs, json.dumps(body), lesson),
        )
        return key

    def progress(
        self,
        key: str,
        *,
        due: str,
        interval: int = 3,
        ease: float = 2.5,
        reps: int = 2,
        lapses: int = 0,
        seen: int = 2,
        retired_at: str | None = None,
        suspended_at: str | None = None,
    ) -> None:
        self.con.execute(
            "INSERT INTO progress(key,ease,interval,reps,lapses,due,last,seen,correct,"
            "wrong,retired_at,retired_reason,suspended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                ease,
                interval,
                reps,
                lapses,
                due,
                due,
                seen,
                seen - lapses,
                lapses,
                retired_at,
                "ganho" if retired_at else None,
                suspended_at,
            ),
        )

    def review(self, key: str, grade: int, day: str) -> None:
        self._clock += 1
        stamp = f"{day}T{self._clock // 60:02d}:{self._clock % 60:02d}:00.000000"
        self.con.execute(
            "INSERT INTO reviews(key,at,day,grade,answer,ms,mode) VALUES(?,?,?,?,?,?,?)",
            (key, stamp, day, grade, "x", 900, "sessao"),
        )

    def pref(self, pack: str, *, rank: int = 0, paused: bool = False) -> None:
        self.con.execute(
            "INSERT INTO pack_prefs(pack,rank,paused_at) VALUES(?,?,?)",
            (pack, rank, "2026-09-01" if paused else None),
        )

    def done(self) -> sqlite3.Connection:
        self.con.commit()
        return self.con


@pytest.fixture
def hub_db(tmp_path):
    def build() -> HubBuilder:
        return HubBuilder(tmp_path / "roda.db")

    return build


def imported(builder: HubBuilder, tmp_path: Path) -> sqlite3.Connection:
    """Run the real importer, and hand back the repetita database it produced."""
    builder.done()
    con = store.connect(tmp_path / "repetita.db")
    hub.import_hub(builder.path, con)
    return con


def both_queues(
    builder: HubBuilder, tmp_path: Path, today: dt.date = TODAY, limit: int = daily.BATCH
) -> tuple[list[str], list[str]]:
    """(predecessor queue, repetita queue) as lists of legacy item keys."""
    legacy_queue, _ = _Legacy.build_session(builder.done(), today, limit)
    con = imported(builder, tmp_path)
    try:
        session = daily.build_session(con, today, limit)
    finally:
        con.close()
    # Card ids are `<key>#<template>`; one hub exercise is one note is one card,
    # so stripping the template is a total, information-free mapping back.
    return legacy_queue, [cid.rsplit("#", 1)[0] for cid in session.cards]


# --------------------------------------------------------------------------
# The transcription is honest
# --------------------------------------------------------------------------


class TestTheTranscriptionIsFaithful:
    """
    Guard rails on `_Legacy`.

    Every tuning constant it claims the predecessor had is one the engine still
    carries at the same value. If someone retunes the engine, these fail and say
    that this file no longer describes the thing it is comparing against --
    rather than the parity tests quietly comparing two copies of the new code.
    """

    def test_queue_constants_are_unchanged(self):
        assert _Legacy.NEW_EVERY == daily.NEW_EVERY
        assert _Legacy.GATE_WINDOW == daily.GATE_WINDOW
        assert _Legacy.GATE_THRESHOLD == daily.GATE_THRESHOLD
        assert _Legacy.GATE_MIN_ANSWERS == daily.GATE_MIN_ANSWERS
        assert _Legacy.LESSON_FRESH_DAYS == daily.LESSON_FRESH_DAYS
        assert _Legacy.LESSON_INTRO_CAP == daily.LESSON_INTRO_CAP
        assert _Legacy.MATURE_DAYS == daily.MATURE_DAYS

    def test_the_pass_threshold_moved_and_that_is_adr_0002(self):
        # The predecessor passed at >= 3 on a 0/2/3/5 scale, which put HARD (2)
        # in the failure branch. The engine passes at >= HARD. This single line
        # is the whole of ADR-0002, and it is why `TestHardIsNowAPass` exists.
        assert _Legacy.PASS_THRESHOLD == 3
        assert Rating.HARD.passed is True
        assert hub.GRADE_MAP[2] is Rating.HARD


class TestTheTypeMapping:
    """
    Every predecessor exercise type, and what it becomes.

    Parity is only meaningful if the content underneath it survived, so the
    mapping from the approved plan is checked here rather than assumed by the
    queue tests above.
    """

    CASES: ClassVar[list[tuple[str, str, str, dict[str, object]]]] = [
        ("gap", "gap", "fill", {"prompt": "Eu ___ bem.", "answers": ["estou"], "cue": "c"}),
        ("cloze", "gap", "fill", {"prompt": "Paranaue ___", "answers": ["parana"]}),
        (
            "choice",
            "gap",
            "fill",
            {"prompt": "Qual?", "answers": ["um"], "options": ["um", "dois", "tres"]},
        ),
        ("traducao", "sentence", "produce", {"prompt": "Okno.", "answers": ["A janela."]}),
        (
            "transformacao",
            "transform",
            "apply",
            {"prompt": "Eu falo.", "instruction": "agora", "answers": ["Eu estou falando."]},
        ),
        ("chunk", "phrase", "say", {"situation": "Chegas.", "target": "Bom dia."}),
    ]

    @pytest.mark.parametrize(("legacy_type", "notetype", "template", "payload"), CASES)
    def test_each_type_becomes_one_note_and_one_card(
        self, hub_db, tmp_path, legacy_type, notetype, template, payload
    ):
        b = hub_db()
        b.item("p.x", type=legacy_type, payload={"id": "x", "type": legacy_type, **payload})
        con = imported(b, tmp_path)
        try:
            note = con.execute("SELECT * FROM notes").fetchone()
            card = con.execute("SELECT * FROM cards").fetchone()
        finally:
            con.close()

        assert hub.NOTETYPE_OF[legacy_type] == notetype
        assert note["notetype"] == notetype
        assert card["id"] == f"p.x#{template}"
        # Every authored value survives the move, under whichever name the note
        # type gives it.
        fields = json.loads(note["fields"])
        assert set(fields) == {hub.FIELD_MAP[notetype][k] for k in payload}
        assert list(fields.values()) == list(payload.values())

    def test_ordem_is_not_a_notetype(self):
        """
        ADR-0001: it was always a way of asking, never a kind of knowledge, so it
        becomes the `wordbank` form and nothing imports as it. An `ordem` row in
        a source database is reported rather than guessed at.
        """
        assert "ordem" not in hub.NOTETYPE_OF
        forms = {
            form
            for notetype in set(hub.NOTETYPE_OF.values())
            for tpl in notetype_of(notetype).cards.values()
            for form in tpl.forms
        }
        assert "wordbank" in forms

    def test_an_unknown_type_is_reported_and_its_history_still_lands(self, hub_db, tmp_path):
        b = hub_db()
        b.item("p.known")
        b.item("p.weird", type="ordem", payload={"id": "weird", "type": "ordem"})
        b.progress("p.weird", due="2026-09-01")
        b.review("p.weird", 3, "2026-09-01")
        b.done()

        con = store.connect(tmp_path / "repetita.db")
        try:
            report = hub.import_hub(b.path, con)
            states = store.all_states(con)
        finally:
            con.close()

        assert any("ordem" in line for line in report.plan.unmapped)
        assert [n.id for n in report.plan.notes] == ["p.known"]
        # The exercise is still there, so this is not the deleted-exercise case:
        # its type is known and simply has no home. Deducing one from its pack
        # would attach real answers to a card that misdescribes them, so the
        # history is reported instead.
        assert report.plan.orphaned == ["p.weird: still in the source, but its type does not map"]
        assert not report.plan.inferred
        assert "p.weird#fill" not in states

    def test_retirement_and_suspension_carry_across(self, hub_db, tmp_path):
        b = hub_db()
        b.progress(b.item("p.gone"), due="2026-09-01", retired_at="2026-09-02")
        b.progress(b.item("p.off"), due="2026-09-01", suspended_at="2026-09-03")
        con = imported(b, tmp_path)
        try:
            retired = store.get_state(con, "p.gone#fill")
            suspended = store.get_state(con, "p.off#fill")
        finally:
            con.close()

        assert retired is not None and suspended is not None
        assert retired.retired_at == "2026-09-02"
        # Translated out of the predecessor's Portuguese, because the project
        # language is English and this value is read by code, not by a learner.
        assert retired.retired_reason == "earned"
        assert suspended.suspended_at == "2026-09-03"
        assert not retired.is_active and not suspended.is_active


# --------------------------------------------------------------------------
# Parity
# --------------------------------------------------------------------------


class TestSameContentSameHistorySameDay:
    def test_a_real_backlog_produces_the_same_queue_in_the_same_order(self, hub_db, tmp_path):
        """
        The headline claim, on the shape of data the predecessor actually held:
        a backlog of overdue cards, some due today, some not due at all, a
        retired one, a suspended one, and new material woven in.
        """
        b = hub_db()
        for i in range(12):
            b.item(f"gram-verbos.v{i:02d}")
        for i in range(6):
            b.item(f"gram-novos.n{i:02d}")

        # Overdue by different amounts. Every due date is distinct on purpose:
        # the two engines break a same-day tie by different keys, and that is
        # pinned separately by `TestDueOrderWithinOneDay` rather than relied on
        # -- or accidentally depended on -- here.
        owed = {
            "gram-verbos.v00": "2026-09-01",
            "gram-verbos.v01": "2026-09-04",
            "gram-verbos.v02": "2026-08-30",
            "gram-verbos.v03": "2026-09-06",
            "gram-verbos.v04": "2026-09-02",
            "gram-verbos.v05": "2026-09-03",
        }
        for key, due in owed.items():
            b.progress(key, due=due)
            b.review(key, 3, "2026-09-01")
        # Not due until next week; must appear in neither queue.
        b.progress("gram-verbos.v06", due="2026-09-12")
        b.review("gram-verbos.v06", 3, "2026-09-02")
        # Out of the queue by two different routes.
        b.progress("gram-verbos.v07", due="2026-09-01", retired_at="2026-09-01")
        b.progress("gram-verbos.v08", due="2026-09-01", suspended_at="2026-09-01")
        for key in ("gram-verbos.v07", "gram-verbos.v08"):
            b.review(key, 3, "2026-09-01")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        # Not vacuous: the queue really does contain the debt, in date order,
        # with new material woven in one per three.
        assert new_queue[:3] == ["gram-verbos.v02", "gram-verbos.v00", "gram-verbos.v04"]
        assert new_queue[3].startswith("gram-novos.")
        assert set(owed) <= set(new_queue)
        assert "gram-verbos.v06" not in new_queue
        assert "gram-verbos.v07" not in new_queue
        assert "gram-verbos.v08" not in new_queue

    def test_a_shut_gate_lets_through_the_same_lesson_material(self, hub_db, tmp_path):
        """
        Gate shut on a bad run: only material from a fresh lesson gets in, capped.

        The fixture keeps every card first answered today inside the fresh lesson
        on purpose -- see `TestTheLessonBudgetIsCountedDifferently`, which is the
        one place the two disagree about the budget.
        """
        b = hub_db()
        for i in range(20):
            b.item(f"licao-2026-09-05.l{i:02d}", lesson="2026-09-05")
        for i in range(5):
            b.item(f"gram-antigo.a{i:02d}")

        # Ten answers, four correct: 40%, well under the 0.75 threshold.
        b.item("gram-antigo.seed")
        b.progress("gram-antigo.seed", due="2026-09-12")
        for i in range(10):
            b.review("gram-antigo.seed", 3 if i < 4 else 0, "2026-09-05")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        assert len(new_queue) == _Legacy.LESSON_INTRO_CAP
        assert all(k.startswith("licao-2026-09-05.") for k in new_queue)

    def test_the_consolidation_pool_comes_out_in_the_same_order(self, hub_db, tmp_path):
        """
        Nothing owed, nothing new: the weakest material, most lapses first.

        `ease` is held equal across the pool. The predecessor broke ties on ease
        before interval and the engine does not, which is a real difference and
        is pinned by `TestConsolidationTiesBreakDifferently` rather than hidden
        by a fixture that never produces the tie.
        """
        b = hub_db()
        shape = [("c0", 1, 5), ("c1", 3, 2), ("c2", 0, 12), ("c3", 3, 9), ("c4", 2, 1)]
        for name, lapses, interval in shape:
            key = b.item(f"gram-cons.{name}")
            b.progress(key, due="2026-09-20", interval=interval, lapses=lapses, ease=2.0)
            b.review(key, 3, "2026-09-01")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        assert new_queue == [
            "gram-cons.c1",
            "gram-cons.c3",
            "gram-cons.c4",
            "gram-cons.c0",
            "gram-cons.c2",
        ]

    def test_the_batch_limit_and_has_more_agree(self, hub_db, tmp_path):
        b = hub_db()
        # Sixty distinct due dates ending today, so the slice is decided by the
        # dates rather than by a tie-break the two engines break differently.
        for i in range(60):
            key = b.item(f"gram-muitos.m{i:02d}")
            b.progress(key, due=(TODAY - dt.timedelta(days=59 - i)).isoformat())
            b.review(key, 3, "2026-09-01")

        legacy_queue, legacy_more = _Legacy.build_session(b.done(), TODAY, 40)
        con = imported(b, tmp_path)
        try:
            session = daily.build_session(con, TODAY, 40)
        finally:
            con.close()

        assert [c.rsplit("#", 1)[0] for c in session.cards] == legacy_queue
        assert session.has_more == legacy_more is True
        assert len(session.cards) == 40

    def test_running_the_import_twice_does_not_change_the_queue(self, hub_db, tmp_path):
        """Idempotency, stated the way it actually matters: the queue is stable."""
        b = hub_db()
        for i in range(8):
            key = b.item(f"gram-idem.i{i}")
            b.progress(key, due=(TODAY - dt.timedelta(days=7 - i)).isoformat())
            b.review(key, 3, "2026-09-02")
        b.done()

        con = store.connect(tmp_path / "repetita.db")
        try:
            hub.import_hub(b.path, con)
            first = daily.build_session(con, TODAY).cards
            hub.import_hub(b.path, con)
            second = daily.build_session(con, TODAY).cards
            counts = {
                t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("notes", "cards", "card_state", "review_log")
            }
        finally:
            con.close()

        assert first == second
        assert counts == {"notes": 8, "cards": 8, "card_state": 8, "review_log": 8}


class TestHistoryIsCopiedNotRecomputed:
    """ADR-0004: the importer transcribes, it does not reschedule."""

    def test_a_schedule_the_hard_bug_reset_is_imported_still_reset(self, hub_db, tmp_path):
        """
        The six cards ADR-0004 is about, in miniature.

        A mature card answered HARD in the predecessor lost its interval and
        gained a lapse. Under today's rules that answer would have advanced it.
        The importer must not care: it copies the record of what happened.
        """
        b = hub_db()
        key = b.item("gram-acento.a1")
        b.progress(key, due="2026-09-06", interval=0, reps=0, lapses=1, ease=2.3, seen=7)
        b.review(key, 2, "2026-09-06")

        con = imported(b, tmp_path)
        try:
            state = store.get_state(con, "gram-acento.a1#fill")
            logged = con.execute(
                "SELECT rating, algo FROM review_log WHERE card_id = ?",
                ("gram-acento.a1#fill",),
            ).fetchone()
        finally:
            con.close()

        assert state is not None
        assert (state.interval, state.lapses) == (0, 1)
        assert state.state["reps"] == 0
        assert state.state["ease"] == pytest.approx(2.3)
        # And it stays findable, which is the escape hatch ADR-0004 promises.
        assert (logged["rating"], logged["algo"]) == (int(Rating.HARD), "sm2-legacy")

    def test_the_scheduler_state_is_carried_across_whole(self, hub_db, tmp_path):
        """
        The predecessor's five scalars are exactly the five `sm2` keeps, so the
        next answer continues the schedule instead of restarting it.
        """
        b = hub_db()
        key = b.item("gram-cont.c1")
        b.progress(key, due="2026-09-06", interval=45, reps=6, lapses=0, ease=2.42, seen=6)
        b.review(key, 3, "2026-09-01")

        con = imported(b, tmp_path)
        try:
            state = store.get_state(con, "gram-cont.c1#fill")
        finally:
            con.close()

        assert state is not None
        assert state.algo == sm2.NAME
        assert set(state.state) == set(sm2.new_state())
        assert state.state["interval"] == 45
        assert state.state["reps"] == 6


# --------------------------------------------------------------------------
# Where the engines deliberately differ
# --------------------------------------------------------------------------


class TestSiblingBurying:
    """ADR-0001. The reason parity holds here is worth stating explicitly."""

    def test_burying_cannot_change_an_imported_queue(self, hub_db, tmp_path):
        """
        Parity above is not luck: every predecessor type maps to a note type with
        exactly one card template, so one exercise is one note is one card, and
        `bury_siblings` has nothing to defer. The mapping is asserted directly
        rather than inferred from a queue that happened to match.
        """
        for legacy_type, notetype in hub.NOTETYPE_OF.items():
            templates = set(notetype_of(notetype).cards)
            assert templates == {hub.PRODUCTION_TEMPLATE[notetype]}, (
                f"{legacy_type} -> {notetype} has siblings {templates}; imported history "
                f"would land on one of several cards and this file's claim would need redoing"
            )

        b = hub_db()
        for i in range(6):
            key = b.item(f"gram-sib.s{i}")
            b.progress(key, due="2026-09-01")
            b.review(key, 3, "2026-09-01")
        b.done()
        con = imported(b, tmp_path)
        try:
            cards = daily.scheduled_cards(con)
            queue = daily.build_session(con, TODAY).cards
        finally:
            con.close()

        assert len({c.note_id for c in cards}) == len(cards)
        kept, buried = daily.bury_siblings(queue, cards)
        assert kept == queue
        assert buried == []

    def test_burying_does_diverge_the_moment_a_note_has_siblings(self):
        """
        And it is a real difference, not a dormant one: as soon as content uses a
        multi-card note type, the engine defers the siblings and the predecessor
        -- which had no way to express a second card of the same note at all --
        would have asked all of them. Nothing imports as that today; everything
        authored after the cutover can.
        """
        cards = [
            daily.QueueCard("casa#recognize", "casa", "01", 0, None),
            daily.QueueCard("casa#produce", "casa", "01", 1, None),
            daily.QueueCard("rua#recognize", "rua", "01", 2, None),
        ]
        queue = ["casa#recognize", "casa#produce", "rua#recognize"]
        kept, buried = daily.bury_siblings(queue, cards)
        assert kept == ["casa#recognize", "rua#recognize"]
        assert buried == ["casa#produce"], "held back from today, not queued behind"


class TestHardIsNowAPass:
    """ADR-0002. Imported state is identical; the next answer is not."""

    def test_the_next_answer_advances_where_the_predecessor_reset(self, hub_db, tmp_path):
        b = hub_db()
        key = b.item("gram-next.n1")
        b.progress(key, due="2026-09-06", interval=45, reps=6, lapses=0, ease=2.5, seen=6)
        b.review(key, 3, "2026-09-01")

        con = imported(b, tmp_path)
        try:
            before = store.get_state(con, "gram-next.n1#fill")
        finally:
            con.close()
        assert before is not None

        # The predecessor: grade 2 is below PASS_THRESHOLD, so it took the
        # failure branch -- interval to zero, reps to zero, a lapse recorded.
        assert _Legacy.PASS_THRESHOLD > 2
        legacy_after = {"interval": 0, "reps": 0, "lapses": 1}

        after = sm2.review(dict(before.state), Rating.HARD, dt.datetime(2026, 9, 6, tzinfo=dt.UTC))

        assert after["interval"] == 54  # 45 * 1.2
        assert after["reps"] == 7
        assert after["lapses"] == 0
        assert after["interval"] != legacy_after["interval"]
        assert after["lapses"] != legacy_after["lapses"]
        # The ease still takes the hit, so repeated near-misses slow the card
        # down -- the difference is that they no longer erase its history.
        assert after["ease"] == pytest.approx(2.5 - sm2.HARD_EASE_PENALTY)


class TestSrsFalseBecomesScheduled:
    """
    The one mapping that is an approximation.

    `srs: false` governed INTRODUCTION only in the predecessor; the engine has a
    single `scheduled` boolean. `hub.scheduling` projects one onto the other as
    "the pack introduces it, or it is already in play", and this is where that is
    checked against the predecessor's actual behaviour.
    """

    def test_an_unanswered_unscheduled_exercise_is_introduced_by_neither(self, hub_db, tmp_path):
        b = hub_db()
        b.item("musica-x.m1", srs=0, track="musica")
        b.item("gram-y.g1")
        legacy_queue, new_queue = both_queues(b, tmp_path)
        assert legacy_queue == new_queue == ["gram-y.g1"]

    def test_an_answered_unscheduled_exercise_is_still_owed_by_both(self, hub_db, tmp_path):
        """
        A song line, once answered, was scheduled like anything else. Marking its
        card `scheduled = 0` would have silently dropped four real cards -- and
        their debt -- out of the queue.
        """
        b = hub_db()
        key = b.item("musica-x.m1", srs=0, track="musica")
        b.progress(key, due="2026-09-01")
        b.review(key, 3, "2026-09-01")
        b.item("musica-x.m2", srs=0, track="musica")

        legacy_queue, new_queue = both_queues(b, tmp_path)
        assert legacy_queue == new_queue == ["musica-x.m1"]

        con = imported(b, tmp_path)
        try:
            flags = dict(con.execute("SELECT id, scheduled FROM cards"))
        finally:
            con.close()
        assert flags == {"musica-x.m1#fill": 1, "musica-x.m2#fill": 0}


class TestIntroductionOrderDiverges:
    """
    Three predecessor behaviours the engine does not reproduce.

    None is a bug in the importer -- they are policy the engine has not grown
    yet -- but a parity test that quietly avoided them would be worthless, so
    each is asserted as a difference with the input that produces it.
    """

    def test_pack_rank_is_not_honoured(self, hub_db, tmp_path):
        """
        `pack_prefs.rank` pushed a pack up or down the introduction order, and
        the real snapshot has two ranked packs. `policies/daily.py` has no
        equivalent, so ordering differs while the set introduced does not.
        """
        b = hub_db()
        for i in range(3):
            b.item(f"aaa-first.f{i}")
        for i in range(3):
            b.item(f"zzz-last.l{i}")
        b.pref("zzz-last", rank=5)

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue[0].startswith("zzz-last."), "rank 5 sorts the pack first"
        assert new_queue[0].startswith("aaa-first."), "the engine sorts by unit name"
        assert legacy_queue != new_queue
        assert sorted(legacy_queue) == sorted(new_queue)

    def test_a_paused_pack_is_not_held_back(self, hub_db, tmp_path):
        b = hub_db()
        b.item("gram-on.a1")
        b.item("gram-off.b1")
        b.pref("gram-off", paused=True)

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == ["gram-on.a1"]
        assert new_queue == ["gram-off.b1", "gram-on.a1"]

    def test_tracks_are_not_interleaved_within_a_lesson(self, hub_db, tmp_path):
        """
        The predecessor round-robined across tracks inside one lesson group so a
        big pack could not crowd the others out. The engine orders by unit and
        position; weighting new material by tag is course configuration it does
        not read yet.
        """
        b = hub_db()
        for i in range(3):
            b.item(f"gram-g.g{i}", track="gramatica", lesson="2026-09-05")
        for i in range(3):
            b.item(f"vocab-v.v{i}", track="vocabulario", lesson="2026-09-05")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        # vocabulario outranks gramatica in NEW_SPLIT, so the predecessor
        # alternates starting from it.
        assert legacy_queue[:4] == ["vocab-v.v0", "gram-g.g0", "vocab-v.v1", "gram-g.g1"]
        assert new_queue[:4] == ["gram-g.g0", "gram-g.g1", "gram-g.g2", "vocab-v.v0"]
        assert sorted(legacy_queue) == sorted(new_queue)


class TestConsolidationTiesBreakDifferently:
    def test_ease_no_longer_breaks_a_tie_before_interval(self, hub_db, tmp_path):
        """
        The predecessor sorted the consolidation pool by (-lapses, ease,
        interval); the engine sorts by (-lapses, interval). With equal lapses and
        equal intervals the predecessor put the lower ease -- the harder card --
        first, and the engine leaves the order to the dictionary.
        """
        b = hub_db()
        for name, ease in (("easy", 2.6), ("hard", 1.4)):
            key = b.item(f"gram-tie.{name}")
            b.progress(key, due="2026-09-20", interval=4, lapses=1, ease=ease)
            b.review(key, 3, "2026-09-01")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == ["gram-tie.hard", "gram-tie.easy"]
        assert new_queue == ["gram-tie.easy", "gram-tie.hard"]


class TestDueOrderWithinOneDay:
    """
    Both engines break a same-day tie deterministically, by different keys.

    Both sort the debt by due date and Python's sort is stable, so ties fall out
    in the order the cards arrived in -- and two cards owed on the same day is
    the common case, not an edge case.

    The predecessor built its list from `SELECT key, track FROM items`, so its
    tie-break was whatever that scan produced: row order, which is insertion
    order here, and item-key order wherever SQLite can answer the query from the
    primary-key index instead -- which is what `SELECT key FROM items` does in
    the real-snapshot comparison below. `daily.scheduled_cards` now orders by
    `n.unit, n.ord, c.id`: content order, the same key `introduction_order`
    breaks its ties with. Before that it had no `ORDER BY` at all and the order
    was the planner's to choose, which is what issue #20 was about.

    Where the two agree the queues are identical, asserted below. Where they do
    not they differ, and that is pinned rather than hidden by a fixture that
    never produces the disagreement.
    """

    def test_a_tie_comes_out_the_same_way_where_the_two_keys_agree(self, hub_db, tmp_path):
        b = hub_db()
        for i in range(6):
            key = b.item(f"gram-tied.t{i}")
            b.progress(key, due="2026-09-04" if i % 2 else "2026-09-02")
            b.review(key, 3, "2026-09-01")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        # Oldest day first, and within a day the order the pack was written in.
        assert new_queue == [
            "gram-tied.t0",
            "gram-tied.t2",
            "gram-tied.t4",
            "gram-tied.t1",
            "gram-tied.t3",
            "gram-tied.t5",
        ]

    def test_the_engine_groups_a_tied_day_by_pack_where_the_predecessor_does_not(
        self, hub_db, tmp_path
    ):
        """
        Two packs written in alternation, everything owed on the same day.

        The engine asks a pack's material together and in the order it was
        written, because that is what content order means; the predecessor asked
        them in the order the rows happen to sit in `items`. Intended: a tie
        should be broken by the course, not by insertion.
        """
        b = hub_db()
        for name in ("x", "y"):
            for pack in ("gram-beta", "gram-alpha"):
                key = b.item(f"{pack}.{name}")
                b.progress(key, due="2026-09-02")
                b.review(key, 3, "2026-09-01")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == ["gram-beta.x", "gram-alpha.x", "gram-beta.y", "gram-alpha.y"]
        assert new_queue == ["gram-alpha.x", "gram-alpha.y", "gram-beta.x", "gram-beta.y"]


class TestTheLessonBudgetIsSpentOnLessonMaterialOnly:
    def test_a_back_catalogue_introduction_does_not_spend_it(self, hub_db, tmp_path):
        """
        With the gate shut, both engines allow at most LESSON_INTRO_CAP fresh
        introductions a day, and both charge that budget for lesson material
        alone.

        The engine used to charge it for every card met for the first time today,
        back catalogue included, which shrank the lesson's allowance for reasons
        that had nothing to do with the lesson (#19). This fixture is the case
        that showed it: five back-catalogue cards met today, and a full budget
        that must survive them.
        """
        b = hub_db()
        for i in range(15):
            b.item(f"licao-2026-09-05.l{i:02d}", lesson="2026-09-05")
        # Five back-catalogue cards met for the first time today. Neither engine
        # charges any of them to the budget: they are not lesson material.
        for i in range(5):
            key = b.item(f"gram-velho.o{i}")
            b.progress(key, due="2026-09-20")
            b.review(key, 3, TODAY.isoformat())
        # Shut the gate: ten answers, two of them correct.
        key = b.item("gram-velho.seed")
        b.progress(key, due="2026-09-20")
        for i in range(10):
            b.review("gram-velho.seed", 3 if i < 2 else 0, "2026-09-05")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        assert len(new_queue) == _Legacy.LESSON_INTRO_CAP

    def test_a_lesson_introduction_made_today_does_spend_it(self, hub_db, tmp_path):
        """The other half: the fresh lesson's own introductions are charged."""
        b = hub_db()
        for i in range(15):
            key = b.item(f"licao-2026-09-05.l{i:02d}", lesson="2026-09-05")
            if i < 5:
                b.progress(key, due="2026-09-20")
                b.review(key, 3, TODAY.isoformat())
        key = b.item("gram-velho.seed")
        b.progress(key, due="2026-09-20")
        for i in range(10):
            b.review("gram-velho.seed", 3 if i < 2 else 0, "2026-09-05")

        legacy_queue, new_queue = both_queues(b, tmp_path)

        assert legacy_queue == new_queue
        assert len(new_queue) == _Legacy.LESSON_INTRO_CAP - 5


# --------------------------------------------------------------------------
# The real thing
# --------------------------------------------------------------------------

SNAPSHOT = Path(
    os.environ.get(
        "REPETITA_HUB_SNAPSHOT",
        Path.home() / "Documents/Research/hub/data/backups/roda-2026-09-06-pre-refactor.db",
    )
)


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    con = store.connect(tmp_path_factory.mktemp("real") / "repetita.db")
    report = hub.import_hub(SNAPSHOT, con)
    yield con, report
    con.close()


@pytest.mark.skipif(not SNAPSHOT.is_file(), reason="the hub snapshot is not on this machine")
class TestAgainstTheRealSnapshot:
    """
    The same claim against 578 real exercises and 454 real answers.

    Skipped where the snapshot is absent, which is everywhere except the author's
    machine -- it is one person's study history and is not in this repository.
    Never run against the live database: that file has no other copy.
    """

    def test_everything_came_across(self, real):
        _, report = real
        assert report.counts == {
            "notes": 578,
            "cards": 578,
            "card_state": 99,
            "review_log": 454,
        }
        assert not report.plan.quarantined
        assert not report.plan.orphaned
        # Sixteen exercises were deleted from the content after being practised.
        assert len(report.plan.inferred) == 16

    def test_the_owed_work_is_identical(self, real):
        """
        The part of the queue that history alone decides, on the real data.

        Compared against the predecessor as a list of (due date, cards owed that
        day). That is what both engines promise *each other*: the same debt,
        oldest day first. It is not a weakening -- the days are compared in order
        and their contents exactly -- it is the strongest true statement, because
        the two break a same-day tie by different keys and always have
        (`TestDueOrderWithinOneDay`).

        The engine's own order is stronger than that, and is asserted here on the
        real data: content order, total, and decided by nothing but the content
        and the schedule. Most of this queue is ties -- thirty-two cards over
        three dates -- so before #20 nearly all of it was the query planner's to
        arrange.

        If the import got a single `due`, `seen`, `retired_at` or `suspended_at`
        wrong, this moves.
        """
        con, _ = real
        legacy = sqlite3.connect(f"file:{SNAPSHOT}?mode=ro", uri=True)
        legacy.row_factory = sqlite3.Row
        try:
            records = {r["key"]: r for r in legacy.execute("SELECT * FROM progress")}
            keys = [r["key"] for r in legacy.execute("SELECT key FROM items")]
            legacy_due = [k for k in keys if _Legacy.is_due(records.get(k), TODAY)]
            legacy_due.sort(key=lambda k: records[k]["due"] or "")
        finally:
            legacy.close()

        states = store.all_states(con)
        cards = daily.scheduled_cards(con)
        new_due = [c.card_id for c in cards if (s := states.get(c.card_id)) and s.is_due(TODAY)]
        new_due.sort(key=lambda cid: states[cid].due or "")
        new_keys = [cid.rsplit("#", 1)[0] for cid in new_due]

        def by_day(keys: list[str]) -> list[tuple[str, list[str]]]:
            return [
                (day, sorted(group))
                for day, group in itertools.groupby(keys, key=lambda k: records[k]["due"])
            ]

        assert set(new_keys) == set(legacy_due)
        assert by_day(new_keys) == by_day(legacy_due)
        assert len(legacy_due) == 32
        assert [day for day, _ in by_day(legacy_due)] == ["2026-09-04", "2026-09-05", "2026-09-06"]

        # The engine's order, element for element: due date first, then content.
        content = {c.card_id: (c.unit, c.ord, c.card_id) for c in cards}
        assert new_due == sorted(new_due, key=lambda cid: (states[cid].due or "", content[cid]))

    def test_the_six_cards_adr_0004_is_about_are_findable(self, real):
        con, _ = real
        rows = con.execute(
            "SELECT card_id FROM review_log WHERE rating = ? AND algo = ?",
            (int(Rating.HARD), "sm2-legacy"),
        ).fetchall()
        assert len(rows) == 7
        assert len({r["card_id"] for r in rows}) == 6
