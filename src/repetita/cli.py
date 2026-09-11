"""
Command line entry point.

Subcommands are added as the engine grows; the ones below are what exists today.
`validate` and `check-ids` are the two that CI depends on, so they are the ones
that must never regress.
"""

from __future__ import annotations

import argparse
import sqlite3
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


def _open_db(args: argparse.Namespace) -> sqlite3.Connection:
    from .store.db import connect, default_path

    return connect(getattr(args, "db", None) or default_path())


def _one_course(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT id FROM courses LIMIT 1").fetchone()
    if row is None:
        raise LookupError("no course in this database -- run `repetita serve` once to import one")
    return str(row["id"])


def _cmd_catalogue(args: argparse.Namespace) -> int:
    from .store.catalogue import SelectorError, catalogue, parse_selector

    con = _open_db(args)
    try:
        try:
            where = parse_selector(args.where)
        except SelectorError as e:
            print(f"catalogue: {e}")
            return 1
        dims = [d.strip() for d in args.group_by.split(",") if d.strip()]
        rows = catalogue(con, group_by=dims, where=where)
    finally:
        con.close()

    if not rows:
        print("nothing matches")
        return 0
    widths = [max(len(r.keys[d]) for r in rows) for d in dims] if dims else []
    for d, w in zip(dims, widths, strict=True):
        print(f"{d:<{w}} ", end="")
    print(f"{'cards':>7} {'notes':>7}")
    for r in rows:
        for d, w in zip(dims, widths, strict=True):
            print(f"{r.keys[d]:<{w}} ", end="")
        print(f"{r.cards:>7} {r.notes:>7}")
    print(f"\n{sum(r.cards for r in rows)} cards in {len(rows)} group(s)")
    return 0


def _cmd_tag(args: argparse.Namespace) -> int:
    from .store import tags as T
    from .store.catalogue import SelectorError, parse_selector

    con = _open_db(args)
    try:
        if args.verb == "list":
            for tag, n in T.inventory(con):
                print(f"{n:6d}  {tag}")
            return 0
        try:
            where = parse_selector(getattr(args, "where", None))
        except SelectorError as e:
            print(f"tag: {e}")
            return 1

        dry = args.dry_run
        if args.verb == "add":
            change = T.add(con, args.tag, where=where, dry_run=dry)
        elif args.verb == "remove":
            change = T.remove(con, args.tag, where=where, dry_run=dry)
        elif args.verb == "rename":
            change = T.rename(con, args.tag, args.to, dry_run=dry)
        elif args.verb == "merge":
            change = T.merge(con, args.tag.split(","), args.to, dry_run=dry)
        elif args.verb == "split":
            if not where:
                print("tag split: --where is required; a split with no selector is a rename")
                return 1
            change = T.split(con, args.tag, args.to, where=where, dry_run=dry)
        else:  # pragma: no cover - argparse restricts this
            raise ValueError(args.verb)
    except ValueError as e:
        print(f"tag: {e}")
        return 1
    finally:
        con.close()

    head = "would change" if dry else "changed"
    print(f"{change.verb} {change.detail}: {head} {change.notes} note(s)")
    for note_id in change.note_ids[:20]:
        print(f"  {note_id}")
    if change.notes > 20:
        print(f"  ... and {change.notes - 20} more")
    if dry and change.notes:
        print("\nnothing written. Re-run without --dry-run to apply.")
    elif change.notes:
        print("\nExport to turn this into a reviewable diff:")
        print("  repetita export <course> --to courses/<course>")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from .store.export import export_course

    con = _open_db(args)
    try:
        course = args.course or _one_course(con)
        files = export_course(con, course, args.to)
    except LookupError as e:
        print(f"export: {e}")
        return 1
    finally:
        con.close()
    print(f"{course} written to {args.to} ({len(files)} files)")
    return 0


def _cmd_reclassify(args: argparse.Namespace) -> int:
    from .store.cards import reclassify

    con = _open_db(args)
    try:
        n = reclassify(con, args.course)
    finally:
        con.close()
    print(f"re-filed the material and re-bucketed {n} card state(s)")
    return 0


def _cmd_issues(args: argparse.Namespace) -> int:
    from .store import issues as I

    con = _open_db(args)
    try:
        if args.resolve is not None:
            issue = I.resolve(con, args.resolve, note=args.note)
            if issue is None:
                print(f"issues: no open issue {args.resolve}")
                return 1
            print(f"resolved #{issue.id}")
            return 0
        if args.raise_:
            issue = I.raise_issue(con, kind=args.kind, body=args.raise_, selector=args.where)
            print(f"raised #{issue.id}")
            return 0
        open_issues = I.open_issues(con)
        if not open_issues:
            print("no open issues")
            return 0
        for issue in open_issues:
            where = f"  [{issue.selector}]" if issue.selector else ""
            print(f"#{issue.id}  {issue.kind}{where}\n    {issue.body}")
        print(f"\n{len(open_issues)} open")
    finally:
        con.close()
    return 0


def _cmd_inbox(args: argparse.Namespace) -> int:
    """Material captured in the app and waiting to be shaped into exercises."""
    from .store import drafts

    con = _open_db(args)
    try:
        if args.show is not None:
            draft = drafts.get(con, args.show)
            if draft is None:
                print(f"inbox: no draft {args.show}")
                return 1
            # Printed byte-for-byte: an agent works from what was written, not
            # from this module's idea of what it meant.
            print(draft.body)
            return 0

        if args.done is not None:
            draft = drafts.done(con, args.done, outcome=args.note)
            if draft is None:
                print(f"inbox: no open draft {args.done}")
                return 1
            print(f"closed #{draft.id}")
            return 0

        if args.discard is not None:
            print("discarded" if drafts.discard(con, args.discard) else "inbox: nothing to discard")
            return 0

        items = drafts.all_drafts(con) if args.all else drafts.queued(con)
        if not items:
            print("nothing waiting" if not args.all else "the inbox is empty")
            return 0
        for draft in items:
            state = "queued" if draft.queued else "done  "
            print(f"#{draft.id:<4} {state}  {draft.created_at[:10]}  {len(draft.body):>5} chars")
            print(f"        {draft.summary}")
            if draft.outcome:
                print(f"        -> {draft.outcome}")
        waiting = sum(1 for d in items if d.queued)
        print(f"\n{waiting} waiting to be shaped")
        print("  repetita inbox --show <id>    read one in full")
        print("  repetita inbox --done <id> --note '...'    close it, saying what was made")
    finally:
        con.close()
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Copy the study database, or say what copies there are."""
    from .store import snapshots

    db = getattr(args, "db", None)
    if args.list:
        found = snapshots.listing(db)
        if not found:
            print(f"no snapshots yet in {snapshots.directory(db)}")
            print("  repetita snapshot 'why'    take one")
            return 0
        for snap in found:
            when = snap.taken_at.strftime("%Y-%m-%d %H:%M")
            size = f"{snap.bytes / 1_048_576:.1f} MB"
            kept = "auto" if snap.automatic else "kept"
            print(f"{snap.name:<46} {when}  {size:>8}  {kept}  {snap.reason}")
        print(f"\n{len(found)} in {snapshots.directory(db)}")
        return 0

    try:
        snap = snapshots.take(db, args.reason or "")
    except FileNotFoundError as e:
        print(f"snapshot: {e}")
        return 1
    print(f"{snap.path}  ({snap.bytes / 1_048_576:.1f} MB)")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    """Put a snapshot back. What is there now is snapshotted first."""
    from .store import snapshots

    from .store.db import default_path

    db = Path(args.db) if getattr(args, "db", None) else default_path()
    try:
        snap = snapshots.restore(args.name, db)
    except LookupError as e:
        print(f"restore: {e}")
        return 1
    print(f"restored {snap.name} over {db}")
    print("what was there a moment ago is beside it, named auto-*-pre-restore")
    return 0


def _cmd_rename_id(args: argparse.Namespace) -> int:
    """Give an exercise a different id, and move its history with it."""
    from .store import snapshots
    from .store.rename import CannotRename, rename

    con = _open_db(args)
    try:
        db = getattr(args, "db", None)
        snap = snapshots.take(db, "pre-rename", automatic=True)
        try:
            moved = rename(con, args.old, args.new)
        except CannotRename as e:
            print(f"rename-id: {e}")
            return 1

        history = (
            f"{moved.answers} answer{'' if moved.answers == 1 else 's'} and "
            f"{moved.states} card state{'' if moved.states == 1 else 's'}"
        )
        print(f"{moved.old} -> {moved.new}")
        print(f"  {moved.cards} card(s) moved, with {history}")
        print(f"  snapshot: {snap.path}")
        recorded = _record_rename(args, con, moved.new, moved.old)
        if recorded:
            print(f"  recorded in {recorded}")
        print()
        print("Now export, so the course file says the same thing:")
        print(f"  repetita export <course> --to {args.courses}/<course>")
    finally:
        con.close()
    return 0


def _record_rename(
    args: argparse.Namespace, con: sqlite3.Connection, note_id: str, old: str
) -> Path | None:
    """
    Write the rename down where `check-ids` will look.

    An id vanishing from `courses/` is the thing CI exists to catch, and a
    rename looks exactly like one. The record is what tells the two apart -- so
    the rule stops being "never rename", which nothing could enforce, and becomes
    "a rename is written down", which CI can.

    Which course to write into is asked of the database, not guessed from the
    directory listing: the note knows which course it belongs to, and `courses/`
    usually holds more than one.
    """
    from .content.renames import record

    row = con.execute("SELECT course FROM notes WHERE id = ?", (note_id,)).fetchone()
    course = row["course"] if row else ""
    root = Path(args.courses)
    if (root / "course.yaml").is_file():
        return record(root, old, note_id)

    where = root / course
    if course and (where / "course.yaml").is_file():
        return record(where, old, note_id)
    print(f"  not recorded: no course directory for {course!r} under {root}")
    return None


def _cmd_purge(args: argparse.Namespace) -> int:
    """Delete material outright, after saying what that costs."""
    from .store import snapshots
    from .store.purge import purge, what_would_go

    con = _open_db(args)
    try:
        where = {
            "note_id": args.note_id,
            "unit": args.set,
            "archived_before": args.archived_before,
        }
        if not any(where.values()):
            print("purge: name an exercise, or --set <unit>, or --archived-before <date>")
            return 2

        going = what_would_go(con, **where)
        if not going:
            print("purge: nothing matches")
            return 0

        print(f"{len(going.notes)} exercise(s), {going.cards} card(s)")
        if going.live:
            print(f"  {len(going.live)} of them are still in the course, not archived")
        print(f"  history attached: {going.answers} answer(s), {going.states} card state(s)")
        print(
            "  history will be deleted too"
            if args.with_history
            else "  history stays, pointing at exercises that no longer exist"
        )

        if not args.yes:
            print()
            print("Nothing was deleted. Add --yes to do it.")
            return 0
        if going.history and args.with_history and not args.i_mean_it:
            print()
            print(
                f"Refusing: --with-history would delete {going.history} rows of study history, "
                "which cannot be rebuilt from anything. Add --i-mean-it."
            )
            return 1

        snap = snapshots.take(getattr(args, "db", None), "pre-purge", automatic=True)
        gone = purge(con, **where, with_history=args.with_history)
        print()
        print(f"deleted {len(gone.notes)} exercise(s) and {gone.cards} card(s)")
        print(f"  snapshot: {snap.path}")
    finally:
        con.close()
    return 0


def _cmd_check_ids(args: argparse.Namespace) -> int:
    from .content.ids import ids_at, ids_in
    from .content.renames import renames

    before = ids_at(args.base, str(args.courses))
    after = ids_in(args.courses)
    gone = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))

    # A rename done properly looks exactly like a disappearance from here, so
    # the record is what tells them apart. `ids_in` namespaces every id as
    # `<course>/<id>`, and a rename is recorded inside its own course.
    recorded: dict[str, str] = {}
    root = Path(args.courses)
    roots = [root] if (root / "course.yaml").is_file() else sorted(root.glob("*"))
    for course in roots:
        if (course / "course.yaml").is_file():
            for was, became in renames(course).items():
                recorded[f"{course.name}/{was}"] = f"{course.name}/{became}"

    # Only when the exercise really is there under its new id. A record pointing
    # at nothing is a claim, not a rename.
    moved = [i for i in gone if recorded.get(i) in after]
    lost = [i for i in gone if i not in moved]

    print(f"{len(before)} ids at {args.base}, {len(after)} now (+{len(added)}, -{len(gone)})")
    if moved:
        print()
        print(f"Renamed, with their history, and recorded ({len(moved)}):")
        for i in moved:
            print(f"  {i} -> {recorded[i]}")
    if not lost:
        return 0

    print()
    print(f"These ids existed at {args.base} and are gone ({len(lost)}):")
    for i in lost:
        print(f"  {i}")
    print()
    print("An id is a scheduling key: a rename by hand silently deletes every")
    print("learner's progress on that item, and nothing in the app reveals it.")
    print("If one of these was renamed, do it with the command that moves the")
    print("history and writes the rename down:")
    print("  repetita rename-id <old> <new>")
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

    cat = sub.add_parser("catalogue", help="count material, grouped by anything")
    cat.add_argument("--group-by", default="topic,state", help="e.g. topic,state or level,track")
    cat.add_argument("--where", default=None, help="e.g. level=A2,state=new")
    cat.add_argument("--db", type=Path, default=None)
    cat.set_defaults(func=_cmd_catalogue)

    t = sub.add_parser("tag", help="add, remove, rename, merge or split a tag")
    t.add_argument("verb", choices=("add", "remove", "rename", "merge", "split", "list"))
    t.add_argument("tag", nargs="?", help="the tag (comma-separated for merge)")
    t.add_argument("--to", default=None, help="the new tag, for rename/merge/split")
    t.add_argument("--where", default=None, help="which notes, e.g. topic=tempo")
    t.add_argument("--dry-run", action="store_true", help="show what would change")
    t.add_argument("--db", type=Path, default=None)
    t.set_defaults(func=_cmd_tag)

    e = sub.add_parser("export", help="write the material back out as a course directory")
    e.add_argument("course", nargs="?", default=None)
    e.add_argument("--to", type=Path, required=True)
    e.add_argument("--db", type=Path, default=None)
    e.set_defaults(func=_cmd_export)

    rc = sub.add_parser("reclassify", help="re-file material and re-bucket card states")
    rc.add_argument("course", nargs="?", default=None)
    rc.add_argument("--db", type=Path, default=None)
    rc.set_defaults(func=_cmd_reclassify)

    iss = sub.add_parser("issues", help="observations about how the material is organised")
    iss.add_argument("--raise", dest="raise_", default=None, metavar="TEXT")
    iss.add_argument(
        "--kind", default="other", help="taxonomy | coverage | balance | duplicate | other"
    )
    iss.add_argument("--where", default=None, help="what you were looking at")
    iss.add_argument("--resolve", type=int, default=None, metavar="ID")
    iss.add_argument("--note", default=None, help="what you changed")
    iss.add_argument("--db", type=Path, default=None)
    iss.set_defaults(func=_cmd_issues)

    ren = sub.add_parser("rename-id", help="change an exercise id, history and all")
    ren.add_argument("old")
    ren.add_argument("new")
    ren.add_argument("--courses", type=Path, default=Path("courses"))
    ren.add_argument("--db", type=Path, default=None)
    ren.set_defaults(func=_cmd_rename_id)

    pur = sub.add_parser("purge", help="delete material outright (archiving is the default)")
    pur.add_argument("note_id", nargs="?", default=None)
    pur.add_argument("--set", default=None, metavar="UNIT", help="every exercise in a set")
    pur.add_argument("--archived-before", default=None, metavar="DATE")
    pur.add_argument("--with-history", action="store_true", help="delete the answers too")
    pur.add_argument("--yes", action="store_true", help="actually do it")
    pur.add_argument("--i-mean-it", action="store_true", help="required to delete history")
    pur.add_argument("--db", type=Path, default=None)
    pur.set_defaults(func=_cmd_purge)

    snap = sub.add_parser("snapshot", help="copy the study database, safely")
    snap.add_argument("reason", nargs="?", default="", help="what you are about to do")
    snap.add_argument("--list", action="store_true", help="show the snapshots there are")
    snap.add_argument("--db", type=Path, default=None)
    snap.set_defaults(func=_cmd_snapshot)

    rest = sub.add_parser("restore", help="put a snapshot back")
    rest.add_argument("name", help="a snapshot name from `repetita snapshot --list`")
    rest.add_argument("--db", type=Path, default=None)
    rest.set_defaults(func=_cmd_restore)

    inbox = sub.add_parser("inbox", help="material captured in the app, waiting to be shaped")
    inbox.add_argument("--show", type=int, default=None, metavar="ID", help="print one in full")
    inbox.add_argument("--done", type=int, default=None, metavar="ID", help="close it")
    inbox.add_argument("--note", default=None, help="what was made from it")
    inbox.add_argument("--discard", type=int, default=None, metavar="ID")
    inbox.add_argument("--all", action="store_true", help="include ones already shaped")
    inbox.add_argument("--db", type=Path, default=None)
    inbox.set_defaults(func=_cmd_inbox)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
