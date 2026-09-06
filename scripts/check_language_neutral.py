#!/usr/bin/env python3
"""
Check that the engine contains no target-language vocabulary.

The rule (CLAUDE.md #4) is about *behaviour*: an identifier, a literal or a
default value that encodes knowledge of one particular language belongs in course
configuration, not in the engine.

It is deliberately NOT about prose. A comment saying "accents are folded because
a course asked for it, not because the engine knows about Portuguese" is the rule
being explained, not broken -- and a naive `grep` flags exactly that, which is
how this script came to exist. So: parse the source, look at identifiers and at
string literals that are not docstrings, and ignore comments entirely.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Vocabulary from the first course's domain. Extend when a new course would
# tempt someone to hardcode something.
FORBIDDEN = re.compile(
    r"portugu[eê]s|vocabulario|gram[aá]tica|aposentado|li[cç][aã]o|"
    r"saudade|capoeira|polski|polish",
    re.IGNORECASE,
)


def _docstrings(tree: ast.AST) -> set[int]:
    """Line numbers of docstring nodes, which are documentation, not behaviour."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(body[0].value.lineno)
    return out


def check(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        text: str | None = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.arg):
            text = node.arg
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            text = node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.lineno in skip:
                continue
            text = node.value

        if text and FORBIDDEN.search(text):
            hits.append((getattr(node, "lineno", 0), text[:80]))
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "src/repetita")
    failures = 0
    for path in sorted(root.rglob("*.py")):
        for lineno, text in check(path):
            print(f"{path}:{lineno}: target-language vocabulary in code: {text!r}")
            failures += 1

    if failures:
        print()
        print("The engine must not know about any particular language.")
        print("Move this into course configuration (courses/<id>/course.yaml)")
        print("or into an i18n catalogue. See CLAUDE.md rule 4.")
        return 1

    print(f"Engine is language-neutral ({root}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
