"""
Ids that were renamed, written down.

`check-ids` fails when an id disappears from `courses/`, and that check is the
most important one in the repository: a rename that nobody noticed silently
deletes a learner's progress. A rename done properly -- through
`repetita rename-id`, which moves the history across nine tables -- looks
identical to CI.

So it is recorded. The rule stops being *never rename an id*, which nothing could
enforce and which left wrong ids wrong for ever, and becomes **a rename is
written down**, which CI can check and a reviewer can read.
"""

from __future__ import annotations

from pathlib import Path

import yaml

FILENAME = "renames.yaml"

HEADER = """\
# Ids that were renamed, oldest first.
#
# `repetita rename-id` writes here, and `repetita check-ids` reads it: an id that
# vanishes from this course is a mistake unless it is written down below. The
# history moved with it -- see ADR-0011.
"""


def path(course_dir: Path | str) -> Path:
    return Path(course_dir) / FILENAME


def renames(course_dir: Path | str) -> dict[str, str]:
    """`{old id: new id}` for one course, or empty if nothing was ever renamed."""
    where = path(course_dir)
    if not where.is_file():
        return {}
    try:
        loaded = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        # A broken record is not a reason to fail a content check; it is a
        # reason for the check to say the rename is not recorded.
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items() if k and v}


def record(course_dir: Path | str, old: str, new: str) -> Path:
    """Add one rename, keeping what is already there."""
    where = path(course_dir)
    known = renames(course_dir)
    # A second rename of the same exercise points the original id at where it
    # ended up, so the chain stays readable from either end.
    for was, became in list(known.items()):
        if became == old:
            known[was] = new
    known[old] = new
    where.write_text(
        HEADER + yaml.safe_dump(known, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )
    return where
