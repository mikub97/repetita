"""
Grader registry.

Third-party graders register through the `roda.graders` entry-point group; the
built-ins are listed here so the common case needs no packaging metadata.
"""

from __future__ import annotations

from ..core.protocols import Grader
from .choice import ChoiceGrader, SelfGrader
from .sentence import SentenceGrader
from .typed import TypedGrader

_BUILTIN: dict[str, Grader] = {
    g.name: g for g in (TypedGrader(), SentenceGrader(), ChoiceGrader(), SelfGrader())
}


def get(name: str) -> Grader:
    try:
        return _BUILTIN[name]
    except KeyError:
        raise LookupError(
            f"unknown grader {name!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None


def names() -> list[str]:
    return sorted(_BUILTIN)


__all__ = ["ChoiceGrader", "SelfGrader", "SentenceGrader", "TypedGrader", "get", "names"]
