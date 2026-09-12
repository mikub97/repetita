"""
A course as a single file, so it can leave and return through a browser.

`export_course` writes a directory and `load_course` reads one, which is the
right shape for `courses/` and for a pull request and the wrong shape for a
download. A zip is the same directory with one name on it: the file somebody
downloads unpacks into exactly what the CLI writes, so there is one format
rather than two that drift.

**This is the only code in the repository that reads a file somebody else made.**
Everything else parses files the author put there. `read_bundle` is written
accordingly: every member is checked before anything is written, and the checks
are refusals rather than repairs. A zip is a list of paths chosen by whoever
built it, and the ones worth refusing -- `../../.ssh/authorized_keys`, an
absolute path, a symlink pointing anywhere it likes -- look exactly like the
ones worth keeping until you look at them.
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from .export import export_course

#: What a course directory is made of. Anything else in the zip is refused
#: rather than ignored: a course carries no executables, no archives and no
#: dotfiles, and "ignored" is how something unexpected becomes something
#: unnoticed.
ALLOWED_SUFFIXES = frozenset({".yaml", ".yml"})

#: Generous for a course and nowhere near enough to be a problem. The largest
#: course in this repository is a few hundred kilobytes.
MAX_MEMBERS = 5_000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class BadBundle(ValueError):
    """A zip that is not a course, said in one sentence a person can act on."""


def write_bundle(con: sqlite3.Connection, course_id: str) -> bytes:
    """
    `course_id` as a zip of its course directory. Raises `LookupError` if absent.

    Goes through `export_course` rather than around it, so a downloaded course
    and a `repetita export` of the same material are the same bytes. Archived
    material is not in it, for the reason `export._notes` gives: exporting it
    would put it back on the next import, which would make removing an exercise
    impossible to express.
    """
    with tempfile.TemporaryDirectory(prefix="repetita-bundle-") as tmp:
        root = Path(tmp) / course_id
        written = export_course(con, course_id, root)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(written):
                zf.write(path, arcname=str(path.relative_to(root.parent)))
        return buffer.getvalue()


def read_bundle(data: bytes, into: Path | str) -> Path:
    """
    Unpack a course zip into `into`, and return the directory that holds it.

    Refuses rather than repairs. Every rejection below has the same shape: the
    member names where it wants to be written, and there is no reading of that
    name under which writing it there is what the author of the zip is entitled
    to ask for.
    """
    root = Path(into).resolve()
    root.mkdir(parents=True, exist_ok=True)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BadBundle("that is not a zip file") from e

    with zf:
        members = zf.infolist()
        if len(members) > MAX_MEMBERS:
            raise BadBundle(f"too many files in the zip ({len(members)})")

        total = 0
        for member in members:
            if member.is_dir():
                continue
            name = member.filename

            # A member may not say where on the disk it goes. `..` and a leading
            # slash are the two ways it can try, and `PurePath` on the *posix*
            # spelling is not enough on its own: a zip written on Windows can
            # carry a backslash, which is a separator there and an ordinary
            # character in a filename here.
            if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
                raise BadBundle(f"unsafe path in the zip: {name}")

            # Symlinks are stored as a regular member whose content is the target
            # path, flagged in the external attributes. Unpacking one creates a
            # door into the rest of the filesystem that the *next* member can
            # then write through, which is why this is refused rather than
            # followed.
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise BadBundle(f"the zip contains a symlink: {name}")

            if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                raise BadBundle(f"a course holds only YAML files, and this has {name}")

            total += member.file_size
            if total > MAX_TOTAL_BYTES:
                raise BadBundle("the course in that zip is implausibly large")
            if member.compress_size and member.file_size / member.compress_size > (
                MAX_COMPRESSION_RATIO
            ):
                raise BadBundle(f"{name} expands far more than a YAML file should")

            # Belt and braces: the checks above should make this unreachable, and
            # it is here because the cost of being wrong about that is somebody
            # else's file overwritten. `resolve` collapses any `..` that survived
            # and follows a directory symlink already on disk.
            target = (root / name).resolve()
            if not target.is_relative_to(root):
                raise BadBundle(f"unsafe path in the zip: {name}")

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())

    return _course_root(root)


def _course_root(root: Path) -> Path:
    """
    Where the course actually is inside what was unpacked.

    A zip made by `write_bundle` holds one top-level directory, because that is
    what somebody sees when they double-click it. One made by zipping a course
    folder's *contents* has `course.yaml` at the top. Both are what the person
    meant, so both are accepted.
    """
    if (root / "course.yaml").is_file() or (root / "units").is_dir():
        return root
    entries = [p for p in root.iterdir() if p.is_dir()]
    if len(entries) == 1:
        return entries[0]
    raise BadBundle("no course in that zip -- expected a course.yaml or a units/ directory")
