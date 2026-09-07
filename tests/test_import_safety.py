"""
Re-importing must never revert an answer given in this engine.

Once studying happens here, the predecessor's state is the older of the two and
the import is a one-way sync. Overwriting a locally advanced card would silently
undo real answers -- the same failure the append-only review log exists to
prevent, arriving through a different door.
"""

import datetime as dt
from pathlib import Path

import pytest

from repetita import srs, store
from repetita.core.types import Rating
from repetita.importers.hub import import_hub, studied_here

SNAPSHOT = Path("/Users/m11/Documents/Research/hub/data/backups/roda-2026-09-06-pre-refactor.db")
AT = dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.UTC)


pytestmark = pytest.mark.skipif(
    not SNAPSHOT.is_file(), reason="the predecessor snapshot is not on this machine"
)


@pytest.fixture
def imported(tmp_path):
    db = tmp_path / "study.db"
    con = store.connect(db)
    import_hub(SNAPSHOT, con)
    return con, db


def test_a_fresh_import_protects_nothing(imported):
    con, _ = imported
    assert studied_here(con) == set()


def test_an_answer_given_here_is_not_reverted_by_a_second_import(imported):
    con, _ = imported
    card_id = next(iter(store.all_states(con)))
    before = store.get_state(con, card_id)

    # Study it here: several passes, so the schedule moves well past whatever
    # the legacy state said.
    for _ in range(4):
        store.record_answer(con, card_id, Rating.GOOD, backend=srs.get("sm2"), at=AT)
    advanced = store.get_state(con, card_id)
    assert advanced.seen > before.seen

    report = import_hub(SNAPSHOT, con)

    assert card_id in report.protected
    kept = store.get_state(con, card_id)
    assert kept.seen == advanced.seen, "a real answer was reverted to the imported state"
    assert kept.due == advanced.due
    assert kept.algo == "sm2"


def test_untouched_cards_are_still_refreshed(imported):
    con, _ = imported
    studied = next(iter(store.all_states(con)))
    store.record_answer(con, studied, Rating.GOOD, backend=srs.get("sm2"), at=AT)

    report = import_hub(SNAPSHOT, con)

    assert report.protected == (studied,)
    assert len(store.all_states(con)) >= 99, "the rest of the import still applied"


def test_a_dry_run_reports_what_it_would_keep_and_writes_nothing(imported):
    con, _ = imported
    card_id = next(iter(store.all_states(con)))
    store.record_answer(con, card_id, Rating.GOOD, backend=srs.get("sm2"), at=AT)
    before = store.get_state(con, card_id)

    report = import_hub(SNAPSHOT, con, dry_run=True)

    assert card_id in report.protected
    assert store.get_state(con, card_id) == before


def test_answers_given_here_are_never_double_counted(imported):
    con, _ = imported
    card_id = next(iter(store.all_states(con)))
    store.record_answer(con, card_id, Rating.GOOD, backend=srs.get("sm2"), at=AT)

    def rows() -> int:
        return int(con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"])

    before = rows()
    import_hub(SNAPSHOT, con)
    assert rows() == before, "re-import must append no history that is already there"
