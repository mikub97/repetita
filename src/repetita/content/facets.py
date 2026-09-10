"""
Reading a course's tags as answers to questions.

A tag is a flat string: `A2`, `gramatica`, `preposicoes`, `licao-setembro`. Four
different kinds of statement in one namespace, and nothing in the engine able to
tell them apart -- so "the A2 grammar cards about prepositions that are still
new" could not be expressed, let alone grouped.

`facets.yaml` says which question each tag answers. The tags themselves do not
change, which is deliberate: `importers/hub.py` records that the predecessor had
`track`, `topic` and `level` as separate columns and the import flattened them
into tags on purpose, because a tag is the general form. An axis is a reading of
that bag, not a replacement for it.

Everything here is a pure function over strings. No language-specific value
appears -- `vocabulario` is course configuration, and the engine never learns it.
"""

from __future__ import annotations

from .models import Facets, Problem

#: One axis is allowed to absorb whatever the others do not claim, so adding a
#: topic is an edit to a note rather than to a course's configuration.
UNPLACED = "unplaced"


def resolve(tag: str, facets: Facets) -> str:
    """Follow a rename. One hop: an alias chain is a mistake, not a feature."""
    return facets.aliases.get(tag, tag)


def axis_of(tag: str, facets: Facets) -> str | None:
    """
    Which axis claims this tag, if any.

    Declared values win over the catch-all, so moving a value onto a real axis is
    what reclassifies it -- no note has to be touched.
    """
    for name, axis in facets.axes.items():
        if tag in axis.values:
            return name
    for name, axis in facets.axes.items():
        if axis.catch_all:
            return name
    return None


def classify(
    tags: tuple[str, ...] | list[str],
    facets: Facets,
    *,
    origin: str = "",
    note_id: str | None = None,
) -> tuple[dict[str, tuple[str, ...]], list[Problem]]:
    """
    Sort a note's tags onto axes.

    Problems here are **never fatal**. A note tagged in a way the course does not
    describe is badly filed, not broken, and quarantining it would take working
    material away from a learner over a bookkeeping mistake -- the opposite of
    what the quarantine is for.
    """
    placed: dict[str, list[str]] = {}
    problems: list[Problem] = []
    unplaced: list[str] = []

    for raw in tags:
        tag = resolve(raw, facets)
        axis = axis_of(tag, facets)
        if axis is None:
            unplaced.append(tag)
            continue
        bucket = placed.setdefault(axis, [])
        if tag not in bucket:
            bucket.append(tag)

    if unplaced:
        problems.append(
            Problem(
                origin=origin,
                note_id=note_id,
                kind="taxonomy",
                detail=(
                    f"no axis claims {', '.join(sorted(unplaced))} -- add the value to an "
                    f"axis in facets.yaml, or mark one axis catch_all"
                ),
                fatal=False,
            )
        )

    for name, values in placed.items():
        cap = facets.axes[name].max_per_note
        if cap is not None and len(values) > cap:
            problems.append(
                Problem(
                    origin=origin,
                    note_id=note_id,
                    kind="taxonomy",
                    detail=f"{name} allows {cap} value(s), got {len(values)}: {', '.join(values)}",
                    fatal=False,
                )
            )

    return {k: tuple(v) for k, v in placed.items()}, problems
