"""
Presenter registry.

A presenter answers "which form should this card be asked in right now" -- a
policy, not five `if` statements in a view. Third-party presenters register
through the `repetita.presenters` entry-point group; the built-ins are listed
here so the common case needs no packaging metadata.
"""

from __future__ import annotations

from ..core.protocols import Presenter
from .ladder import LadderPresenter

_BUILTIN: dict[str, Presenter] = {p.name: p for p in (LadderPresenter(),)}

#: Used when nothing has asked for a particular one. `fixed` and `varied` are
#: still to come; until a course can name its presenter, this is the choice.
DEFAULT = "ladder"


def get(name: str | None = None) -> Presenter:
    key = name or DEFAULT
    try:
        return _BUILTIN[key]
    except KeyError:
        raise LookupError(
            f"unknown presenter {key!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None


def names() -> list[str]:
    return sorted(_BUILTIN)


__all__ = ["DEFAULT", "LadderPresenter", "get", "names"]
