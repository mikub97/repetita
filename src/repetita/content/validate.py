"""
Refusing to serve broken material.

The bug this package was built around was a cue reading `fim de semana = weekend`
for the answer `fim de semana`: the exercise handed over its own solution. It
happened because `prompt` and `hint` were each doing two jobs -- posing the task
AND restating the rule.

A leaking exercise is worse than a missing one. It is answered correctly every
time, so it inflates measured accuracy, which opens the new-material gate, which
floods the learner with material they have not earned. And it is invisible: it
looks like a card you know well.

So a leak is a QUARANTINE, not a warning. The predecessor logged a console
warning and 70 of 330 items leaked anyway.

The check runs per (note, card), not per note. Since a field can be the question
for one card and the answer for another -- `l1` in a `vocab` note is the prompt
for `produce` and the answer for `recognize` -- asking "does this note leak?" has
no single answer. Asking "does this card leak?" does.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import Note, NoteType, Problem

_TAGS = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")

#: Fields that are allowed to contain the answer because that is their job: the
#: options of a multiple choice, and the wrong answers offered beside it. They
#: are served through the form layer, which knows to shuffle them, rather than
#: through the general "show these fields" path.
ANSWER_BEARING = frozenset({"options", "distractors"})

MIN_CHOICE_OPTIONS = 3


def _plain(value: Any) -> str:
    """Visible text only: drop markup, collapse whitespace, lowercase."""
    return _WS.sub(" ", _TAGS.sub(" ", str(value or ""))).strip().lower()


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _contains_word(haystack: str, needle: str) -> bool:
    """Whole-word containment, so `a` does not match inside `casa`."""
    if not needle.strip():
        return False
    return (
        re.search(r"(?<![0-9a-zà-ÿ])" + re.escape(needle) + r"(?![0-9a-zà-ÿ])", haystack)
        is not None
    )


def _values(note: Note, name: str) -> list[str]:
    v = note.fields.get(name)
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def find_leaks(note: Note, nt: NoteType) -> list[Problem]:
    """
    Report every field that gives away its own card's answer.

    Comparison is accent-SENSITIVE, deliberately. `esta` and `está` are different
    words: "Ele ___ doente esta semana" with the answer `está` is a legitimate
    exercise, not a leak. Folding accents first would bury real leaks under a
    pile of false positives like that one, so an accent-only match is reported as
    a warning and the note still serves.
    """
    problems: list[Problem] = []

    def report(detail: str, fatal: bool) -> None:
        problems.append(
            Problem(origin=note.origin, note_id=note.id, kind="leak", detail=detail, fatal=fatal)
        )

    for template, tpl in nt.cards.items():
        answers = [_plain(a) for a in _values(note, tpl.expect)]
        if not answers:
            continue
        for fname in nt.visible_before(template):
            if fname in ANSWER_BEARING:
                continue
            for shown_value in _values(note, fname):
                text = _plain(shown_value)
                if not text:
                    continue
                for answer, raw in zip(answers, _values(note, tpl.expect), strict=False):
                    if not answer:
                        continue
                    if text == answer:
                        report(
                            f"card {template!r}: {fname!r} is identical to the answer "
                            f"{raw!r} -- nothing left to do",
                            True,
                        )
                    elif _contains_word(text, answer):
                        report(
                            f"card {template!r}: {fname!r} contains the answer {raw!r}. "
                            f"Move the solved form into a field shown after answering "
                            f"(explain), or describe the word instead of naming it.",
                            True,
                        )
                    elif _contains_word(_strip_accents(text), _strip_accents(answer)):
                        report(
                            f"card {template!r}: {fname!r} differs from {raw!r} only by accents",
                            False,
                        )
    return problems


def check_shape(note: Note, nt: NoteType) -> list[Problem]:
    """Rules that are about the exercise working, not about its fields existing."""
    problems: list[Problem] = []

    def report(kind: str, detail: str, fatal: bool = True) -> None:
        problems.append(
            Problem(origin=note.origin, note_id=note.id, kind=kind, detail=detail, fatal=fatal)
        )

    options = _values(note, "options")
    if options:
        answers = {_plain(a) for tpl in nt.cards.values() for a in _values(note, tpl.expect)}
        if len(options) < MIN_CHOICE_OPTIONS:
            report("shape", f"a choice needs at least {MIN_CHOICE_OPTIONS} options")
        if len({_plain(o) for o in options}) != len(options):
            report("shape", "choice options must be unique")
        hits = [o for o in options if _plain(o) in answers]
        if len(hits) != 1:
            report("shape", f"exactly one option must be the answer (found {len(hits)})")

    # A gap with no narrowing hint usually accepts several correct words:
    # "Compro ___ na feira" fits verduras, frutas, peixe. Not fatal -- sometimes
    # the sentence itself forces the answer -- but worth a look.
    if "cue" in nt.fields and "prompt" in nt.fields:
        prompt = note.text("prompt")
        if "___" in prompt and not note.text("cue") and "(" not in prompt:
            report("ambiguous", "a gap with no 'cue' may accept other words", fatal=False)

    return problems


def check(note: Note, nt: NoteType) -> list[Problem]:
    return find_leaks(note, nt) + check_shape(note, nt)
