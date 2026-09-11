"""
Copies of the study database, taken correctly.

`card_state` and `review_log` are the only things in this system that cannot be
rebuilt from anything -- the material comes back from `courses/`, the schedule
does not. Everything else in this package is careful because of that, and this
module is what makes the carefulness affordable: with a snapshot in hand, a
mistake is a restore rather than a loss, and an operation that would otherwise
have to be forbidden can simply be offered.

**Taken through SQLite's own backup API, never `cp`.** The database runs in WAL
mode, where committed rows live in the log until a checkpoint folds them into the
main file. A file copy takes the main file, so whether it is complete depends on
whether a checkpoint has happened -- it is a race, not a guarantee, and the
losing side is a backup that opens cleanly and is quietly missing the last
answers. `Connection.backup()` has no race: it copies a consistent view of a
database that is open and being written to, which is the only moment anybody
reaches for a backup.

Checked rather than assumed: the seven hand-made copies already in `data/` all
open and all look sane -- they won the race. `tests/store/test_snapshots.py`
shows the losing side, which takes an open connection and fifty committed rows to
provoke. That is the argument for this module: not that the old copies are
broken, but that nobody should have to be lucky.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .db import default_path

#: Where they live. Under `data/`, which is already outside git -- one learner's
#: history is not reproducible from this repository and does not belong in it.
DIRECTORY = "snapshots"

#: How many automatic snapshots to keep. Enough to cover a session's worth of
#: mistakes; few enough that the directory stays readable. Snapshots taken by
#: hand are never pruned -- somebody typed a reason for those.
KEEP_AUTOMATIC = 20

#: Automatic ones say so in their name, so that pruning can tell them apart from
#: a copy somebody took deliberately before doing something frightening.
AUTO = "auto-"

_UNSAFE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    taken_at: datetime
    reason: str
    automatic: bool

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def bytes(self) -> int:
        return self.path.stat().st_size


def directory(db: Path | str | None = None) -> Path:
    """Where snapshots of `db` go: a `snapshots/` folder beside it."""
    return (Path(db) if db else default_path()).parent / DIRECTORY


def slug(reason: str) -> str:
    return _UNSAFE.sub("-", reason.strip().lower()).strip("-")[:40]


def take(
    db: Path | str | None = None,
    reason: str = "",
    *,
    automatic: bool = False,
    at: datetime | None = None,
) -> Snapshot:
    """
    Copy the database as it stands, consistently, and return where it went.

    Safe to call while the app is running and somebody is answering: that is the
    whole point of the backup API over a file copy.
    """
    source = Path(db) if db else default_path()
    if not source.exists():
        raise FileNotFoundError(f"no database at {source}")

    stamp = (at or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    why = slug(reason)
    name = f"{AUTO if automatic else ''}{stamp}{'-' + why if why else ''}.db"
    into = directory(source)
    into.mkdir(parents=True, exist_ok=True)
    path = into / name

    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(path))
    try:
        # One call, and SQLite holds the read lock it needs for as long as it
        # needs it. Nothing here has to know about WAL, checkpoints or readers.
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()

    if automatic:
        prune(source)
    return _describe(path)


def listing(db: Path | str | None = None) -> list[Snapshot]:
    """Every snapshot, newest first."""
    into = directory(db)
    if not into.is_dir():
        return []
    return sorted(
        (_describe(p) for p in into.glob("*.db")),
        key=lambda s: s.taken_at,
        reverse=True,
    )


def find(name: str, db: Path | str | None = None) -> Snapshot | None:
    """By file name, with or without the `.db`."""
    wanted = name if name.endswith(".db") else f"{name}.db"
    return next((s for s in listing(db) if s.name == wanted), None)


def restore(name: str, db: Path | str | None = None) -> Snapshot:
    """
    Put a snapshot back, having first taken one of what is there now.

    The snapshot-before-restore is not ceremony: restoring is itself the
    destructive operation, and the state it overwrites is usually the one nobody
    thought worth keeping until a second later.
    """
    target = Path(db) if db else default_path()
    chosen = find(name, target)
    if chosen is None:
        known = ", ".join(s.name for s in listing(target)[:5]) or "none"
        raise LookupError(f"no snapshot {name!r}; most recent: {known}")

    if target.exists():
        take(target, "pre-restore", automatic=True)

    src = sqlite3.connect(str(chosen.path))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return chosen


def prune(db: Path | str | None = None, keep: int = KEEP_AUTOMATIC) -> int:
    """Drop the oldest automatic snapshots. Never touches a deliberate one."""
    automatic = [s for s in listing(db) if s.automatic]
    doomed = automatic[keep:]
    for snapshot in doomed:
        snapshot.path.unlink(missing_ok=True)
    return len(doomed)


def _describe(path: Path) -> Snapshot:
    """
    Read a snapshot's name back: `[auto-]YYYYmmdd-HHMMSS[-reason]`.

    Anything that does not parse is still listed -- the seven copies already in
    `data/` were named by hand, and a file this cannot read is exactly the file
    somebody might need. It falls back to the modification time and shows the
    whole name as the reason.
    """
    stem = path.stem
    automatic = stem.startswith(AUTO)
    rest = stem[len(AUTO) :] if automatic else stem
    parts = rest.split("-")
    try:
        when = datetime.strptime("-".join(parts[:2]), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        reason = "-".join(parts[2:])
    except (ValueError, IndexError):
        when = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        reason = rest
    return Snapshot(path=path, taken_at=when, reason=reason, automatic=automatic)
