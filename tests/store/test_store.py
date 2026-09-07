import datetime as dt
import json
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating

UTC = dt.UTC
AT = dt.datetime(2026, 9, 6, 21, 30, tzinfo=UTC)
DAY = dt.date(2026, 9, 6)

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def course(tmp_path):
    def build(notes_yaml):
        root = tmp_path / "course"
        (root / "units" / "01" / "notes").mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(COURSE)
        (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(notes_yaml))
        return load_course(root)

    return build


TWO_NOTES = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
    """


class TestSync:
    def test_content_lands_in_the_cache(self, con, course):
        notes, cards = store.sync(con, course(TWO_NOTES))
        assert (notes, cards) == (2, 4)
        assert sorted(store.card_ids(con)) == [
            "casa#produce",
            "casa#recognize",
            "rua#produce",
            "rua#recognize",
        ]

    def test_syncing_twice_is_idempotent(self, con, course):
        result = course(TWO_NOTES)
        store.sync(con, result)
        store.sync(con, result)
        assert len(store.card_ids(con)) == 4

    def test_sync_never_touches_card_state(self, con, course):
        result = course(TWO_NOTES)
        store.sync(con, result)
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        before = store.get_state(con, "casa#produce")

        store.sync(con, result)

        assert store.get_state(con, "casa#produce") == before

    def test_a_card_whose_note_disappears_keeps_its_history(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=backend, at=AT)

        # The author removes the note. Its schedule must survive, so that putting
        # it back does not reset months of work.
        store.sync(
            con,
            course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
            """),
        )

        assert "rua#produce" not in store.card_ids(con)
        assert store.get_state(con, "rua#produce") is not None


class TestRecordAnswer:
    def test_one_answer_writes_exactly_one_log_row(self, con, course):
        store.sync(con, course(TWO_NOTES))
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        rows = con.execute("SELECT * FROM review_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["rating"] == int(Rating.GOOD)
        assert rows[0]["card_id"] == "casa#produce"

    def test_the_state_that_preceded_the_answer_is_logged(self, con, course):
        # Without this, FSRS cannot be tuned and no scheduler can be evaluated.
        # It cannot be reconstructed later, which is why it is written from the
        # first answer rather than added when someone needs it.
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT)
        rows = con.execute("SELECT state_before FROM review_log ORDER BY id").fetchall()
        assert json.loads(rows[0]["state_before"])["reps"] == 0
        assert json.loads(rows[1]["state_before"])["reps"] == 1

    def test_counters_are_kept_as_columns_not_read_out_of_state(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        for rating in (Rating.GOOD, Rating.AGAIN, Rating.GOOD):
            store.record_answer(con, "casa#produce", rating, backend=backend, at=AT)
        cs = store.get_state(con, "casa#produce")
        assert (cs.seen, cs.correct, cs.wrong, cs.lapses) == (3, 2, 1, 1)

    def test_due_is_denormalised_so_the_queue_never_parses_state(self, con, course):
        store.sync(con, course(TWO_NOTES))
        cs = store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        assert cs.due == DAY.isoformat()  # first learning step: back this session

    def test_wrong_answers_are_kept(self, con, course):
        # In a year the best distractors are the mistakes real learners made.
        store.sync(con, course(TWO_NOTES))
        store.record_answer(
            con,
            "casa#produce",
            Rating.AGAIN,
            backend=srs.get("sm2"),
            at=AT,
            answer="a rua",
        )
        assert con.execute("SELECT answer FROM review_log").fetchone()["answer"] == "a rua"

    def test_a_naive_datetime_is_refused(self, con, course):
        store.sync(con, course(TWO_NOTES))
        with pytest.raises(ValueError, match="aware"):
            store.record_answer(
                con,
                "casa#produce",
                Rating.GOOD,
                backend=srs.get("sm2"),
                at=dt.datetime(2026, 9, 6, 21, 30),
            )

    def test_the_local_day_is_separate_from_the_utc_instant(self, con, course):
        # 21:30 in Bahia is 00:30 UTC the next day. Deriving the day from the
        # instant files the session under tomorrow; the predecessor shipped
        # exactly that bug.
        store.sync(con, course(TWO_NOTES))
        late = dt.datetime(2026, 9, 7, 0, 30, tzinfo=UTC)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=srs.get("sm2"),
            at=late,
            local_day=DAY,
        )
        assert con.execute("SELECT day FROM review_log").fetchone()["day"] == "2026-09-06"

    def test_elapsed_days_is_measured_not_assumed(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        later = dt.date(2026, 9, 20)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=backend,
            at=dt.datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
            local_day=later,
        )
        rows = con.execute("SELECT elapsed_days FROM review_log ORDER BY id").fetchall()
        assert rows[0]["elapsed_days"] is None
        assert rows[1]["elapsed_days"] == 14.0


class TestQueries:
    def test_recent_ratings_are_newest_first(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        for rating in (Rating.AGAIN, Rating.GOOD, Rating.EASY):
            store.record_answer(con, "casa#produce", rating, backend=backend, at=AT)
        assert store.recent_ratings(con, 2) == [Rating.EASY, Rating.GOOD]

    def test_first_seen_counts_introductions_not_answers(self, con, course):
        store.sync(con, course(TWO_NOTES))
        backend = srs.get("sm2")
        yesterday = dt.date(2026, 9, 5)
        store.record_answer(
            con,
            "casa#produce",
            Rating.GOOD,
            backend=backend,
            at=AT,
            local_day=yesterday,
        )
        # Reviewed again today -- not a new introduction.
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        store.record_answer(con, "rua#produce", Rating.GOOD, backend=backend, at=AT, local_day=DAY)
        assert store.first_seen_on(con, DAY) == 1
        assert store.count_on(con, DAY) == 2

    def test_a_new_card_is_not_due(self, con, course):
        store.sync(con, course(TWO_NOTES))
        cs = store.CardState(card_id="x", algo="sm2", algo_version=1, state={})
        assert not cs.is_due(DAY), "new cards are introduced under a gate, not owed"


class TestSchema:
    def test_reopening_an_existing_database_is_safe(self, tmp_path, course):
        path = tmp_path / "t.db"
        c1 = store.connect(path)
        store.sync(c1, course(TWO_NOTES))
        store.record_answer(c1, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        c1.close()

        c2 = store.connect(path)
        assert store.get_state(c2, "casa#produce") is not None
        assert len(store.card_ids(c2)) == 4
        c2.close()

    def test_schema_version_is_recorded(self, con):
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(row["value"]) >= 1
