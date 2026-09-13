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


def get(name: str | None = None, *, steps: int | None = None) -> Presenter:
    """
    The presenter to ask a card through.

    `steps` is how many encounters are taught rather than examined, and it is a
    per-request setting rather than a property of the registry: one learner wants
    a gentle first contact and another wants to be tested from the first sight,
    and both are asking the same presenter for a different depth. Only the ladder
    takes it; a presenter that does not understand depth is returned as it is,
    rather than being handed an argument it has no meaning for.
    """
    key = name or DEFAULT
    try:
        found = _BUILTIN[key]
    except KeyError:
        raise LookupError(
            f"unknown presenter {key!r}; available: {', '.join(sorted(_BUILTIN))}"
        ) from None
    if steps is None or not isinstance(found, LadderPresenter):
        return found
    return LadderPresenter(steps)


def names() -> list[str]:
    return sorted(_BUILTIN)


__all__ = ["DEFAULT", "LadderPresenter", "get", "names"]
