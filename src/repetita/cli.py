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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .content.loader import LoadResult
    from .content.models import Course

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

        # A card whose note type declares `choice` but cannot resolve three
        # distractors is a content problem, not a rendering one: the form is
        # simply not offered, so the author loses an exercise without being told.
        thin = _thin_choices(result)
        if thin:
            print(
                f"  {len(thin)} card(s) declare a multiple choice but cannot fill one "
                f"-- they are asked another way"
            )
            if args.strict:
                for card_id in thin[:10]:
                    print(f"    {card_id}")
                failed = True

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


def _note_file(root: Path, unit: str, origin: str) -> Path | None:
    """
    The authored file a note came from.

    `Note.origin` is a bare filename (`loader.py` sets it from `path.name`) and a
    unit may hold its notes either in a `notes/` subdirectory or directly, so the
    path is probed in the loader's own precedence order rather than joined. A
    guess that silently points at nothing would be worse than no path at all.
    """
    for candidate in (root / "units" / unit / "notes" / origin, root / "units" / unit / origin):
        if candidate.is_file():
            return candidate
    return None


def _note_line(path: Path, note_id: str) -> int | None:
    """
    The line the note starts on, for a human's editor.

    A textual scan rather than a parser: `yaml.safe_load` discards line numbers,
    and a loader that kept them would be a second implementation of what a note
    is -- which is the thing `content/CLAUDE.md` warns against. The line is a
    convenience, and its failure mode is printing the path without one.
    """
    wanted = {f"id: {note_id}", f"- id: {note_id}", f'id: "{note_id}"', f"id: '{note_id}'"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for n, line in enumerate(lines, 1):
        if line.strip() in wanted:
            return n
    return None


def _cmd_reports(args: argparse.Namespace) -> int:
    """Exercises the learner flagged as broken, with the file to go and fix."""
    from . import store
    from .content.loader import load_course

    con = store.connect(args.db)

    if args.resolve is not None:
        store.resolve_report(con, args.resolve, _now().date())
        print(f"#{args.resolve} resolved")
        return 0

    reports = store.all_reports(con) if args.all else store.open_reports(con)
    if args.reason:
        reports = [r for r in reports if r.reason == args.reason]
    if not reports:
        # Nothing to do is not an error.
        print("no open reports")
        return 0

    root = _resolve_course(args.course)
    live = {}
    if root is not None:
        result = load_course(root)
        live = {n.id: n for n in result.notes}

    # `other` first: they are the ones a human has to read rather than act on.
    reports.sort(key=lambda r: (r.reason != "other", r.id))

    for r in reports:
        mark = "" if r.resolved_at is None else f"  (resolved {r.resolved_at})"
        print(f"#{r.id}  {r.reason:16s} {r.card_id}   {r.day}  asked as {r.form}{mark}")

        note = live.get(r.note_id)
        unit, origin = (note.unit, note.origin) if note else (r.unit, r.origin)
        path = _note_file(root, unit, origin) if root and unit and origin else None
        if path is not None:
            line = _note_line(path, r.note_id)
            print(f"    {path}{f':{line}' if line else ''}")
        else:
            print(f"    (file not found for note {r.note_id!r})")

        for key, value in r.fields.items():
            text = " / ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            print(f"    {key:12s} {text}")
        if r.given:
            print(f"    {'your answer':12s} {r.given}")
        if r.note:
            print(f"    {'note':12s} {r.note}")
        if note is not None and note.fields != r.fields:
            # The most useful line here: "you already fixed this" as against
            # "this is still broken".
            print("    -- the note has changed since this was reported --")
        print()

    print(f"{len(reports)} report(s)")
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


def _fallback_course(course_id: str) -> Course:
    from .content.models import Course, LanguageSpec, LicenseSpec

    return Course(
        id=course_id,
        l2=LanguageSpec(code="pt", variant="pt-BR"),
        l1=LanguageSpec(code="pl"),
        license=LicenseSpec(name="CC BY-SA 4.0"),
    )


def _thin_choices(result: LoadResult) -> list[str]:
    from .content.distractors import MIN_OPTIONS, build

    counts: dict[str, int] = {}
    for d in build(result.cards, result.notes, result.notetypes):
        counts[d.card_id] = counts.get(d.card_id, 0) + 1
    wants_choice = {
        c.id
        for c in result.cards
        if "choice" in result.notetypes[c.notetype].cards[c.template].forms
    }
    return sorted(cid for cid in wants_choice if counts.get(cid, 0) < MIN_OPTIONS - 1)


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

    r = sub.add_parser("reports", help="exercises reported broken while studying")
    r.add_argument("course", type=Path, nargs="?", default=Path("courses"))
    r.add_argument("--db", type=Path, default=None, help="study database (default: $REPETITA_DB)")
    r.add_argument("--all", action="store_true", help="include reports already resolved")
    r.add_argument("--reason", default=None, help="only this reason code")
    r.add_argument("--resolve", type=int, default=None, metavar="ID", help="mark one dealt with")
    r.set_defaults(func=_cmd_reports)

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
