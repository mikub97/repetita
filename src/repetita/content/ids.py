"""
What an item id is: how one is made, and why none may ever change.

An id is a scheduling key. Renaming one silently deletes every learner's progress
on that item: the card reappears as new, months of history gone, and nothing in
the interface reveals it happened. It is the worst failure mode a YAML-backed SRS
has, and it is invisible in code review -- a rename looks like tidying up.

So it is checked mechanically, against the base branch, on every pull request
(`ids_in`, `ids_at`). Adding and removing ids is legitimate; a removal is
reported so it is a decision rather than an accident.

The same rule is what makes `propose` careful. Since exercises can be written in
the app (ADR-0010) an id now gets made without anybody typing one, and the
moment it is saved it is permanent. So it is derived from the answer -- the one
part of an exercise that says what it is -- and checked against every id the
database has ever used, archived ones included.
"""

from __future__ import annotations

import re
import subprocess
import tarfile
import tempfile
import unicodedata
from collections.abc import Container
from pathlib import Path

from .loader import load_course

#: Long enough to stay readable in a course file, short enough that a whole
#: sentence as an answer does not become a 90-character filename.
MAX_SLUG = 40

_NOT_ALLOWED = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """
    A readable, ASCII, lowercase-kebab handle for a piece of text.

    Accents are folded here and only here -- unlike the leak rule, where `esta`
    and `está` must stay different words. An id is a filename-shaped label, not
    a comparison, and `licao.esta` is easier to live with in a shell than
    `licao.está`.
    """
    folded = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return _NOT_ALLOWED.sub("-", folded).strip("-")[:MAX_SLUG].strip("-")


def propose(unit: str, answer: str, taken: Container[str]) -> str:
    """
    An id for a new exercise: `<unit>.<answer>`, made unique.

    `taken` should be every id the database has ever held, **including archived
    ones** -- an archived note still owns its history, and handing its id to
    something else would hand over the history with it.

    Numbered rather than random when it collides, because two exercises that
    answer the same word are usually a pair, and `genero-o` beside `genero-o-2`
    reads as one.
    """
    stem = slug(answer) or "item"
    base = f"{slug(unit) or 'set'}.{stem}"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


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
