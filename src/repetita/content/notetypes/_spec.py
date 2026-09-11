"""
Shorthand for declaring a field, so that a type reads as a list of decisions.

Its own module rather than the package's `__init__`, which imports every type and
would make that a cycle.
"""

from __future__ import annotations

from ..models import FieldSpec


def field(type_: str = "text", *, required: bool = False, visibility: str = "before") -> FieldSpec:
    """
    One field of a note type.

    `visibility` is the anti-leak contract: a field marked "before" is on screen
    while the question is open, so it must never hold the answer. There is no
    third option -- adding a field means choosing one.
    """
    return FieldSpec(type=type_, required=required, visibility=visibility)  # type: ignore[arg-type]
