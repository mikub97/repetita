"""
A written comment about the app, saved as a file.

Deliberately not a table. The people this exists for are testing a prototype on
their own laptops and reaching the author through git: a comment has to end up
as a file in the working tree, on its way to a commit, and a row in a database
that only they can read would need an export step to do the same job. So it is
written where it will be committed from, the moment it is written.

What that trades away, said plainly rather than discovered later: a comment is
lost if the app is pointed at a different working tree, and a web request writes
into the repository. Both are fine for three people on three laptops and neither
would be fine on a server.

One file per comment, named by the moment it was written and the screen it was
written from, under a directory per person -- so two testers who push on the
same day cannot conflict, and neither can two comments in the same minute.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

#: Who is using this copy. One name per installation, set by whatever starts it.
#: Not `user_id`, which stays 1 everywhere until there is a login: this names a
#: folder, and nothing else reads it.
USER = "REPETITA_USER"

#: Where comments go. Defaults beside the courses, which is inside the checkout.
DIRECTORY = "REPETITA_FEEDBACK"

MAX = 20_000


class Empty(ValueError):
    """A comment with nothing in it. Saving one would be a file nobody wrote."""


def who() -> str:
    return slug(os.environ.get(USER, "") or "me")


def slug(text: str) -> str:
    """
    A name safe to use as a directory, always non-empty.

    It comes from the environment, so it is not trusted to be a path component:
    `../../etc` names a person as readily as `mama` does, and this is the only
    place a person's name reaches the filesystem.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()
    return text[:40] or "me"


def directory(course_dir: Path | str | None = None) -> Path:
    """
    Where the comments live.

    `REPETITA_FEEDBACK` wins. Otherwise beside `courses/`, which is inside the
    checkout the launcher commits from -- and not the working directory, which
    for anything double-clicked is wherever the desktop happens to be.
    """
    if named := os.environ.get(DIRECTORY):
        return Path(named)
    if course_dir:
        return Path(course_dir).resolve().parent.parent / "feedback"
    return Path.cwd() / "feedback"


def save(
    text: str,
    *,
    course_dir: Path | str | None = None,
    where: str = "",
    course: str = "",
    version: str = "",
    user: str | None = None,
    at: datetime | None = None,
) -> Path:
    """
    Write one comment. Returns the file.

    The frontmatter is what makes a vague comment actionable three days later:
    "this is confusing" means one thing on the Study tab and another on Manage,
    and neither the writer nor the reader will remember which.
    """
    body = text.strip()
    if not body:
        raise Empty("nothing to save")
    body = body[:MAX]

    stamp = at or datetime.now().astimezone()
    person = slug(user) if user else who()
    into = directory(course_dir) / person
    into.mkdir(parents=True, exist_ok=True)

    name = f"{stamp:%Y-%m-%d-%H%M%S}-{slug(where) if where else 'app'}.md"
    path = into / name
    # Two comments in the same second on the same screen: rare, and losing one
    # of them would be exactly the failure this module exists against.
    n = 2
    while path.exists():
        path = into / f"{name[:-3]}-{n}.md"
        n += 1

    head = [
        "---",
        f"from: {person}",
        f"at: {stamp.isoformat(timespec='seconds')}",
        *([f"where: {where}"] if where else []),
        *([f"course: {course}"] if course else []),
        *([f"version: {version}"] if version else []),
        "---",
        "",
    ]
    # Written whole and then moved into place, so a reader -- git, most likely --
    # never sees half a comment.
    temporary = path.with_suffix(".part")
    temporary.write_text("\n".join(head) + body + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path
