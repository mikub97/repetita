"""
Reporting an exercise as broken.

A report is a claim about the *material*, and the tests here exist to pin the
three ways it differs from everything else the store records: it never reaches
the review log, it suspends rather than retires, and it survives the content
rebuild that wipes the very rows it describes. The last one is the point of the
feature -- a report that cannot say what was on screen is not actionable, and by
the time anyone reads it the file has usually been edited.
"""

import datetime as dt
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.store.reports import Snapshot

UTC = dt.UTC
AT = dt.datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
DAY = dt.date(2026, 9, 10)

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom
    """


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def course(tmp_path):
    def build(notes=NOTES):
        root = tmp_path / "course"
        directory = root / "units" / "01" / "notes"
        directory.mkdir(parents=True, exist_ok=True)
        (root / "course.yaml").write_text(COURSE)
        (directory / "n.yaml").write_text(textwrap.dedent(notes))
        return load_course(root)

    return build


@pytest.fixture
def backend():
    return srs.get("sm2")


def a_snapshot(**over):
    base = dict(
        note_id="casa",
        template="produce",
        form="typein",
        fields={"l2": "a casa", "l1": "dom"},
        origin="n.yaml",
        unit="01",
    )
    return Snapshot(**{**base, **over})


CARD = "casa#produce"


class TestReporting:
    def test_it_takes_the_card_out_of_the_queue(self, con, backend):
        store.report_card(con, CARD, "wrong_answer", DAY, backend=backend, snapshot=a_snapshot())
        state = store.get_state(con, CARD)
        assert state is not None
        assert state.suspended_at == DAY.isoformat()
        assert state.is_active is False

    def test_it_suspends_rather_than_retires(self, con, backend):
        # Two different lanes that both remove a card, meaning opposite things.
        # Retired is "done with this"; suspended is "not now". A broken exercise
        # is not one the learner has finished with, and if a content bug looked
        # like progress the queue would be quietly wrong about how much is left.
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        state = store.get_state(con, CARD)
        assert state.suspended_at is not None
        assert state.retired_at is None
        assert state.retired_reason is None

    def test_it_writes_nothing_to_the_review_log(self, con, backend):
        # The log is a record of answers given. A report is not an answer, and
        # letting it in would corrupt every accuracy figure computed from it --
        # including the gate that decides how fast new material arrives.
        before = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        store.report_card(con, CARD, "ambiguous", DAY, backend=backend, snapshot=a_snapshot())
        after = con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"]
        assert after == before

    def test_a_card_never_answered_can_be_reported(self, con, backend):
        # The commonest way to meet a broken exercise is on its first showing,
        # when there is no state row to hold `suspended_at`. One is created.
        assert store.get_state(con, CARD) is None
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        state = store.get_state(con, CARD)
        assert state is not None and state.seen == 0
        assert state.suspended_at == DAY.isoformat()

    def test_it_keeps_what_the_learner_typed(self, con, backend):
        # For `also_correct` this is the whole report: the word to add to
        # `answers:` is the one just rejected.
        store.record_answer(
            con, CARD, Rating.AGAIN, backend=backend, at=AT, local_day=DAY, answer="a casinha"
        )
        report = store.report_card(
            con, CARD, "also_correct", DAY, backend=backend, snapshot=a_snapshot()
        )
        assert report.given == "a casinha"

    def test_a_reason_it_does_not_know_is_refused(self, con, backend):
        # Never folded into `other`: a report nobody can act on is worse than no
        # report, because it looks like one.
        with pytest.raises(ValueError):
            store.report_card(con, CARD, "vibes", DAY, backend=backend, snapshot=a_snapshot())
        assert store.open_report_count(con) == 0


class TestItSurvivesTheRebuild:
    def test_a_report_outlives_the_content_it_describes(self, con, course, backend):
        # The test this feature exists for. `sync` wipes notes and cards on every
        # load; a report that pointed at them would describe whatever the file
        # says now -- quite possibly the text written to fix it.
        store.sync(con, course())
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())

        edited = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa bonita
                l1: dom
            """)
        store.sync(con, edited)

        # The cache now says one thing and the report still says another, which
        # is exactly the divergence the snapshot exists to hold.
        cached = con.execute("SELECT fields FROM notes WHERE id = 'casa'").fetchone()
        assert "a casa bonita" in cached["fields"]

        report = store.open_reports(con)[0]
        assert report.fields["l2"] == "a casa"
        assert report.origin == "n.yaml"

    def test_the_suspension_survives_it_too(self, con, course, backend):
        store.sync(con, course())
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        store.sync(con, course())
        assert store.get_state(con, CARD).suspended_at is not None


class TestWithdrawing:
    def test_the_report_can_be_taken_back(self, con, backend):
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        store.withdraw_report(con, CARD)
        assert store.open_report_count(con) == 0
        assert store.get_state(con, CARD).suspended_at is None

    def test_withdrawing_costs_the_card_nothing(self, con, backend):
        # Saying "actually that exercise is fine" is not the same as getting it
        # wrong, and must not cost an interval.
        store.record_answer(
            con, CARD, Rating.GOOD, backend=backend, at=AT, local_day=DAY, answer="a casa"
        )
        before = store.get_state(con, CARD)
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        store.withdraw_report(con, CARD)
        after = store.get_state(con, CARD)
        assert (after.due, after.interval, after.seen, after.lapses) == (
            before.due,
            before.interval,
            before.seen,
            before.lapses,
        )

    def test_a_second_open_report_keeps_the_card_suspended(self, con, backend):
        # Two reports are two complaints. Answering one does not answer the other.
        store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        store.report_card(con, CARD, "ambiguous", DAY, backend=backend, snapshot=a_snapshot())
        store.withdraw_report(con, CARD)
        assert store.open_report_count(con, card_id=CARD) == 1
        assert store.get_state(con, CARD).suspended_at is not None


class TestResolving:
    def test_resolving_keeps_the_row_and_frees_the_card(self, con, backend):
        # Withdrawing removes the row, resolving keeps it. The difference is
        # whether the report was right: a fixed exercise leaves a record of
        # having been broken, a mistaken tap should not.
        report = store.report_card(con, CARD, "typo", DAY, backend=backend, snapshot=a_snapshot())
        store.resolve_report(con, report.id, DAY)
        assert store.open_report_count(con) == 0
        assert len(store.all_reports(con)) == 1
        assert store.all_reports(con)[0].resolved_at == DAY.isoformat()
        assert store.get_state(con, CARD).suspended_at is None
