#!/usr/bin/env python3
"""
Write `docs/schema.md` from the live `SCHEMA` string.

The schema is not copied into the documentation, it is *read out of it*. The
`CREATE TABLE` statements in `store/db.py` already carry the reasoning in their
comments -- "NEVER deleted: see ADR-0006", "non-NULL: changed here, so an import
must not clobber it" -- and those comments are the documentation. A hand-written
schema page would lose them on the first edit and then quietly rot, which is
worse than no page at all: a generated file that drifts is believed.

    python scripts/gen_schema_doc.py           # write the page
    python scripts/gen_schema_doc.py --check   # fail if it is out of date (CI)
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _db_module():
    """
    Load `store/db.py` on its own, without importing the package.

    `import repetita.store.db` runs `store/__init__.py`, which pulls in pydantic
    and most of the engine -- so this script would need the project installed to
    read a string constant out of one file. `db.py` imports nothing but the
    standard library, so it can be loaded directly, and the CI job that checks
    this page needs no dependencies at all.
    """
    path = ROOT / "src" / "repetita" / "store" / "db.py"
    spec = importlib.util.spec_from_file_location("repetita_store_db", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_db = _db_module()
MIGRATIONS, SCHEMA, SCHEMA_VERSION = _db.MIGRATIONS, _db.SCHEMA, _db.SCHEMA_VERSION

PAGE = ROOT / "docs" / "schema.md"

#: Tables whose rows are a person's study history rather than course material.
#: Called out on the page because the difference decides what may be rebuilt.
PROGRESS = {"card_state", "review_log", "known_marks"}

HEAD = """\
# The database

<!--
  GENERATED FILE -- do not edit.
  Written by scripts/gen_schema_doc.py from the SCHEMA string in
  src/repetita/store/db.py. Edit the schema there; CI checks this page matches.
-->

SQLite, one file, **schema version {version}**. {tables} tables, and the whole
of it is in [`store/db.py`]({source}).

Two things explain most of the shape of it.

**Content and progress are different kinds of data.** `notes`, `cards` and
`units` are *material*: since [ADR-0006][adr6] the database owns them, an import
merges into them, and material that leaves the
course is archived rather than deleted. `card_state` and `review_log` are
*history*: they are never rebuilt from anything, ever. That asymmetry is the one
rule you cannot break here.

**There are no foreign keys, on purpose.** A card whose note has gone keeps its
history, and a `card_state` row outlives whatever it was about. Enforcing
referential integrity would mean deleting someone's schedule to keep the
database tidy.

## The tables

[adr6]: architecture/decisions/0006-the-database-owns-the-material.md
"""

ERD_HEAD = """
## How they join

Foreign keys are not declared -- these are the joins the queries actually make.

```mermaid
erDiagram
"""


def tables(schema: str) -> list[tuple[str, str, str]]:
    """(name, body, leading comment) for every table, in declaration order."""
    out = []
    for m in re.finditer(
        # `\);` at the end of a line, so that a one-line table (`meta`) is a
        # table and not the opening of the next one.
        r"((?:^--[^\n]*\n)*)CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);[ \t]*$",
        schema,
        re.S | re.M,
    ):
        comment, name, body = m.group(1), m.group(2), m.group(3)
        prose = "\n".join(line.lstrip("- ").rstrip() for line in comment.strip().splitlines())
        out.append((name, body.rstrip(), prose))
    return out


def columns(body: str) -> list[tuple[str, str]]:
    """
    (declaration, notes) per column, constraints included.

    A comment *above* a column belongs to it and is kept: those are the ones
    carrying the reasoning -- "Made or renamed here rather than read out of a
    directory" -- and dropping them would leave the page a list of types, which
    anyone can already get from `.schema`.
    """
    rows = []
    said: list[str] = []
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line:
            continue
        if line.startswith("--"):
            said.append(line.lstrip("-").strip())
            continue
        decl, _, trailing = line.partition("--")
        note = " ".join([*said, trailing.strip()]).strip()
        said = []
        # A pipe would end the cell early and silently eat the rest of the line.
        rows.append((" ".join(decl.split()).rstrip(","), note.replace("|", "\\|")))
    return rows


def erd(schema: str) -> str:
    """The joins, written by hand because they are not declared anywhere."""
    return (
        ERD_HEAD
        + """    courses ||--o{ units : "has"
    courses ||--o{ notes : "has"
    units ||--o{ notes : "notes.unit = units.id"
    notes ||--o{ cards : "one note, several cards (ADR-0001)"
    notes ||--o{ note_facets : "tags, read as axes"
    cards ||--o| card_state : "never rebuilt"
    cards ||--o{ review_log : "one row per answer"
    plans ||--o{ plan_priorities : "ordered"
    plans ||--o{ plan_revisions : "what an answer was filed under"
    notes ||--o{ pending_changes : "staged, until Confirm"
    units ||--o{ pending_changes : "a removed set is staged the same way"
```

`material_drafts` joins nothing. That is the point of it: raw material waiting to
be shaped, belonging to no course until an agent has made exercises from it
([ADR-0009](architecture/decisions/0009-material-is-captured-before-it-is-shaped.md)).
"""
    )


def render() -> str:
    parts = [
        HEAD.format(
            version=SCHEMA_VERSION,
            tables=len(tables(SCHEMA)),
            source="https://github.com/mikub97/repetita/blob/main/src/repetita/store/db.py",
        )
    ]
    for name, body, prose in tables(SCHEMA):
        kind = " *(history — never rebuilt)*" if name in PROGRESS else ""
        parts.append(f"\n### `{name}`{kind}\n")
        if prose:
            parts.append(f"\n{prose}\n")
        parts.append("\n| column | notes |\n| --- | --- |\n")
        for decl, note in columns(body):
            parts.append(f"| `{decl}` | {note} |\n")
    parts.append(erd(SCHEMA))
    parts.append(
        "\n## Migrations\n\n"
        "Every version is one entry in `MIGRATIONS`, and `SCHEMA` above is the "
        "cumulative result of applying all of them. A fresh database gets "
        "`SCHEMA`; an existing one gets the migrations it has not seen. Both "
        "paths have to end in the same place, which is why the contract is "
        "written down and not merely intended.\n\n"
        f"There are {len(MIGRATIONS)} of them, the most recent taking the "
        f"schema to version {SCHEMA_VERSION}.\n"
    )
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the page is out of date")
    args = ap.parse_args()

    want = render()
    if args.check:
        have = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if have != want:
            print(
                "docs/schema.md is out of date.\n"
                "The schema changed and the page did not. Run:\n"
                "    python scripts/gen_schema_doc.py",
                file=sys.stderr,
            )
            return 1
        print("docs/schema.md matches the schema")
        return 0

    PAGE.write_text(want, encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)} ({len(want.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
