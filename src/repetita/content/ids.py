"""
Item-id stability.

An id is a scheduling key. Renaming one silently deletes every learner's progress
on that item: the card reappears as new, months of history gone, and nothing in
the interface reveals it happened. It is the worst failure mode a YAML-backed SRS
has, and it is invisible in code review -- a rename looks like tidying up.

So it is checked mechanically, against the base branch, on every pull request.
Adding and removing ids is legitimate; a removal is reported so it is a decision
rather than an accident.
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path

from .loader import load_course


def ids_in(courses_dir: Path) -> dict[str, str]:
    """Every note id under a courses directory, mapped to its course."""
    out: dict[str, str] = {}
    if not courses_dir.is_dir():
        return out
    roots = (
        [courses_dir]
        if (courses_dir / "course.yaml").is_file()
        else [p for p in sorted(courses_dir.glob("*")) if (p / "course.yaml").is_file()]
    )
    for root in roots:
        result = load_course(root)
        cid = result.course.id if result.course else root.name
        for note in result.notes:
            out[f"{cid}/{note.id}"] = cid
    return out


def ids_at(ref: str, courses_dir: str = "courses") -> dict[str, str]:
    """The same, as of a git ref. An absent directory means an empty set."""
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "base.tar"
        proc = subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(archive), ref, courses_dir],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            # The path did not exist at that ref -- which is the normal case for
            # the commit that first adds a course.
            return {}
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        return ids_in(Path(tmp) / courses_dir)
