"""
Copies of the study database.

The test this file exists for is `test_a_snapshot_of_a_busy_database_is_whole`.
Copying a WAL-mode database with `cp` is a race: committed rows sit in the log
until a checkpoint folds them into the main file, so a copy is complete only if
it happens to be taken after one. The seven hand-made backups in `data/` all open
and all look right -- they won it. This provokes the losing side deterministically
with an open connection and fifty committed rows, because a backup that is
correct by luck is the kind that fails on the day it is needed.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime

import pytest

from repetita import store
from repetita.store import snapshots


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "study.db"
    con = store.connect(path)
    con.execute("INSERT INTO meta(key, value) VALUES('greeting', 'ola')")
    con.commit()
    con.close()
    return path


def rows(path, table="meta"):
    """How many rows a database file has -- read from the file, not a fixture."""
    con = sqlite3.connect(str(path))
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


class TestTaking:
    def test_it_copies_what_is_there(self, db):
        snap = snapshots.take(db, "before something")
        assert snap.path.exists()
        assert rows(snap.path) == rows(db)

    def test_a_snapshot_of_a_busy_database_is_whole(self, db, tmp_path):
        # The reason this module exists. A connection is open and has committed
        # rows that live in the write-ahead log; `cp` of the main file misses
        # them, the backup API does not.
        con = store.connect(db)
        for i in range(50):
            con.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (f"k{i}", "v"))
        con.commit()

        snap = snapshots.take(db, "mid-write")
        naive = tmp_path / "naive.db"
        shutil.copyfile(db, naive)
        whole = rows(db)
        con.close()

        assert rows(snap.path) == whole, "the snapshot has every committed row"
        assert rows(naive) < whole, (
            "if a plain copy sees them all, WAL is off and this test proves nothing"
        )

    def test_the_name_says_when_and_why(self, db):
        snap = snapshots.take(db, "Pre Rename!", at=datetime(2026, 9, 11, 17, 3, 1, tzinfo=UTC))
        assert snap.name == "20260911-170301-pre-rename.db"
        assert snap.reason == "pre-rename"
        assert not snap.automatic

    def test_an_automatic_one_says_so(self, db):
        assert snapshots.take(db, "pre-purge", automatic=True).automatic

    def test_a_missing_database_is_refused_rather_than_faked(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            snapshots.take(tmp_path / "nothing.db", "why")


class TestListing:
    def test_newest_first(self, db):
        old = snapshots.take(db, "one", at=datetime(2026, 9, 1, tzinfo=UTC))
        new = snapshots.take(db, "two", at=datetime(2026, 9, 2, tzinfo=UTC))
        assert [s.name for s in snapshots.listing(db)] == [new.name, old.name]

    def test_a_hand_named_copy_is_still_listed(self, db):
        # The seven already in `data/` are named things like
        # `repetita-2026-09-10-pre-v2.db`. A file this cannot parse is exactly
        # the file somebody might need.
        into = snapshots.directory(db)
        into.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(db, into / "repetita-2026-09-10-pre-v2.db")
        found = snapshots.listing(db)
        assert [s.name for s in found] == ["repetita-2026-09-10-pre-v2.db"]
        assert not found[0].automatic, "not ours, so never pruned"


class TestPruning:
    def test_it_keeps_the_newest_automatic_ones(self, db):
        for i in range(5):
            snapshots.take(db, "auto", automatic=True, at=datetime(2026, 9, i + 1, tzinfo=UTC))
        assert snapshots.prune(db, keep=2) == 3
        assert len(snapshots.listing(db)) == 2

    def test_it_never_touches_one_taken_by_hand(self, db):
        # Somebody typed a reason for those.
        kept = snapshots.take(db, "before the big import", at=datetime(2026, 1, 1, tzinfo=UTC))
        snapshots.take(db, "auto", automatic=True, at=datetime(2026, 9, 1, tzinfo=UTC))
        snapshots.prune(db, keep=0)
        assert [s.name for s in snapshots.listing(db)] == [kept.name]


class TestRestoring:
    def test_it_puts_the_old_state_back(self, db):
        before = rows(db)
        snap = snapshots.take(db, "before")
        con = store.connect(db)
        con.execute("INSERT INTO meta(key, value) VALUES('later', 'x')")
        con.commit()
        con.close()
        assert rows(db) == before + 1

        snapshots.restore(snap.name, db)
        assert rows(db) == before

    def test_it_snapshots_what_it_is_about_to_overwrite(self, db):
        # Restoring is itself the destructive operation, and the state it
        # overwrites is usually the one nobody thought worth keeping.
        snap = snapshots.take(db, "before")
        con = store.connect(db)
        con.execute("INSERT INTO meta(key, value) VALUES('later', 'x')")
        con.commit()
        con.close()
        overwritten = rows(db)

        snapshots.restore(snap.name, db)
        rescue = [s for s in snapshots.listing(db) if s.reason == "pre-restore"]
        assert len(rescue) == 1
        assert rows(rescue[0].path) == overwritten, "what was overwritten is recoverable"

    def test_an_unknown_name_says_what_there_is(self, db):
        snapshots.take(db, "one")
        with pytest.raises(LookupError, match="most recent"):
            snapshots.restore("nonsense", db)
