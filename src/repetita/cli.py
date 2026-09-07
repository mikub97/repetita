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
from .importers.hub import DEFAULT_COURSE_ID as IMPORT_COURSE_ID


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


def _resolve_course(root: Path) -> Path | None:
    """
    Accept either a course directory or the directory that holds them.

    Serving needs exactly one course, so an ambiguous argument is refused with
    the list rather than resolved by picking the alphabetically first.
    """
    if (root / "course.yaml").is_file():
        return root
    found = sorted(p for p in root.glob("*") if (p / "course.yaml").is_file())
    if len(found) == 1:
        return found[0]
    if not found:
        print(f"no course found under {root}")
    else:
        print(f"several courses under {root}; name the one to serve:")
        for path in found:
            print(f"  {path}")
    return None


def _cmd_serve(args: argparse.Namespace) -> int:
    from .web import create_app

    root = _resolve_course(args.course)
    if root is None:
        return 1

    app = create_app(root, db_path=args.db)
    library = app.extensions["repetita"]
    print(f"{library.course.id}: {len(library.notes)} notes -> {len(library.cards)} cards")
    if library.quarantined:
        # Quarantined material is not served at all; saying so here is the only
        # place a learner would find out without running `validate`.
        print(f"  {library.quarantined} note(s) quarantined -- run `repetita validate` for detail")
    print(f"http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


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


def _cmd_import_hub(args: argparse.Namespace) -> int:
    from .importers.hub import import_hub, render
    from .store.db import connect, default_path

    # `connect` creates the database if it is missing, and a dry run must leave
    # nothing behind -- not even an empty file. Against a target that does not
    # exist yet there is no prior import to diff against, so an in-memory one
    # gives exactly the same answer.
    target: Path | str = args.db or default_path()
    if args.dry_run and not Path(target).is_file():
        target = ":memory:"

    con = connect(target)
    try:
        report = import_hub(args.source, con, course_id=args.course_id, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError) as e:
        print(f"import-hub: {e}")
        return 1
    finally:
        con.close()

    print(render(report, verbose=args.verbose))

    if args.emit_course and not args.dry_run:
        from .importers.emit import emit_course

        files = emit_course(
            report.plan.as_load_result().course or _fallback_course(args.course_id),
            report.plan.notes,
            args.emit_course,
        )
        print(f"\ncourse written to {args.emit_course} ({len(files)} files)")
        print("  serve it with: repetita serve " + str(args.emit_course))
    elif args.emit_course:
        print(f"\n--dry-run: no course written to {args.emit_course}.")

    if args.dry_run:
        print("\n--dry-run: nothing was written.")
    return 0


def _fallback_course(course_id: str):
    from .content.models import Course, LanguageSpec, LicenseSpec

    return Course(
        id=course_id,
        l2=LanguageSpec(code="pt", variant="pt-BR"),
        l1=LanguageSpec(code="pl"),
        license=LicenseSpec(name="CC BY-SA 4.0"),
    )


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

    s_ = sub.add_parser("serve", help="run the study session in a browser")
    s_.add_argument("course", type=Path, nargs="?", default=Path("courses"))
    s_.add_argument("--host", default="127.0.0.1")
    s_.add_argument("--port", type=int, default=5116)
    s_.add_argument("--db", type=Path, default=None, help="study database (default: $REPETITA_DB)")
    s_.add_argument("--debug", action="store_true")
    s_.set_defaults(func=_cmd_serve)

    i = sub.add_parser("import-hub", help="import material and history from a hub database")
    i.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        help="a hub directory, or the SQLite file inside one. Point this at a "
        "COPY: a live study database is one person's history and nothing "
        "recreates it.",
    )
    i.add_argument("--db", type=Path, default=None, help="target database (default: REPETITA_DB)")
    i.add_argument("--dry-run", action="store_true", help="print the diff and write nothing")
    i.add_argument("--course-id", default=IMPORT_COURSE_ID, help="course the notes belong to")
    i.add_argument("--verbose", action="store_true", help="list every reported item, not the first")
    i.add_argument(
        "--emit-course",
        type=Path,
        metavar="DIR",
        help="also write the material as a course directory. The content tables are "
        "a cache rebuilt from disk on every start, so this is what makes the "
        "imported material actually servable.",
    )
    i.set_defaults(func=_cmd_import_hub)

    c = sub.add_parser("check-ids", help="fail if an existing item id disappeared")
    c.add_argument("--base", default="origin/main", help="git ref to compare against")
    c.add_argument("--courses", type=Path, default=Path("courses"))
    c.set_defaults(func=_cmd_check_ids)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
