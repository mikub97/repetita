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


def _now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roda", description="Roda language-learning engine")
    parser.add_argument("--version", action="version", version=f"roda {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schedulers", help="list available scheduler backends").set_defaults(
        func=_cmd_schedulers
    )
    sub.add_parser("graders", help="list available graders").set_defaults(func=_cmd_graders)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
