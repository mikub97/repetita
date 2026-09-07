"""
Command line entry point.

Subcommands are added as the engine grows; the ones below are what exists today.
`validate` and `check-ids` are the two that CI depends on, so they are the ones
that must never regress.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, graders, srs


def _cmd_schedulers(_: argparse.Namespace) -> int:
    for name in srs.names():
        backend = srs.get(name)
        default = " (default)" if name == srs.DEFAULT else ""
        supports_r = backend.retrievability(backend.new_state(), _now()) is not None
        print(f"  {name:10s} v{backend.version}{default}")
        print(f"{'':13s}retrievability: {'yes' if supports_r else 'not supported'}")
    return 0


def _cmd_graders(_: argparse.Namespace) -> int:
    for name in graders.names():
        g = graders.get(name)
        print(f"  {name:10s} accepts: {', '.join(sorted(g.accepts))}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .content.loader import load_course

    roots = [args.course]
    if not (args.course / "course.yaml").is_file():
        roots = sorted(p for p in args.course.glob("*") if (p / "course.yaml").is_file())
        if not roots:
            print(f"no course found under {args.course}")
            return 1

    failed = False
    for root in roots:
        result = load_course(root)
        name = result.course.id if result.course else root.name
        print(f"{name}: {len(result.notes)} notes -> {len(result.cards)} cards")

        if result.fatal:
            print(f"\n  QUARANTINED -- not served until fixed ({len(result.fatal)}):")
            for p in result.fatal:
                print(f"    {p}")
            failed = True
        if result.warnings:
            print(f"\n  warnings ({len(result.warnings)}):")
            for p in result.warnings:
                print(f"    {p}")
        if args.strict and result.warnings:
            failed = True
    return 1 if failed else 0


def _cmd_check_ids(args: argparse.Namespace) -> int:
    from .content.ids import ids_at, ids_in

    before = ids_at(args.base, str(args.courses))
    after = ids_in(args.courses)
    gone = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))

    print(f"{len(before)} ids at {args.base}, {len(after)} now (+{len(added)}, -{len(gone)})")
    if not gone:
        return 0

    print()
    print(f"These ids existed at {args.base} and are gone ({len(gone)}):")
    for i in gone:
        print(f"  {i}")
    print()
    print("An id is a scheduling key. If one of these was RENAMED, restore the old")
    print("id and change only the text -- a rename silently deletes every learner's")
    print("progress on that item, and nothing in the app reveals it.")
    print("If the removal is deliberate, say so in the pull request.")
    return 1


def _now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repetita", description="Repetita -- a spaced-repetition learning engine"
    )
    parser.add_argument("--version", action="version", version=f"repetita {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schedulers", help="list available scheduler backends").set_defaults(
        func=_cmd_schedulers
    )
    sub.add_parser("graders", help="list available graders").set_defaults(func=_cmd_graders)

    v = sub.add_parser("validate", help="check course content")
    v.add_argument("course", type=Path, nargs="?", default=Path("courses"))
    v.add_argument("--strict", action="store_true", help="treat warnings as failures")
    v.set_defaults(func=_cmd_validate)

    c = sub.add_parser("check-ids", help="fail if an existing item id disappeared")
    c.add_argument("--base", default="origin/main", help="git ref to compare against")
    c.add_argument("--courses", type=Path, default=Path("courses"))
    c.set_defaults(func=_cmd_check_ids)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
