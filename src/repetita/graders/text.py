"""
Shared text handling for graders.

Kept separate from any one grader because the normalisation rules are the part
that must be identical everywhere: a course that folds accents must fold them in
the typed grader, the sentence grader, and the leak detector alike, or the three
will disagree about what counts as the same word.
"""

from __future__ import annotations

import re
import unicodedata

from ..core.protocols import GradingOptions

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[.,;:!?¡¿\"“”„«»…()\[\]]")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(s: str, opts: GradingOptions, *, fold_accents: bool = False) -> str:
    """
    Collapse a string to its comparable form.

    `fold_accents` is a separate argument from `opts.fold_accents` on purpose:
    graders need to compare *both* ways in one pass -- exact first, accent-folded
    second -- to tell "wrong word" apart from "right word, missing tilde".
    """
    out = _WS.sub(" ", (s or "").strip())
    if opts.ignore_case:
        out = out.lower()
    for a, b in opts.equivalences:
        out = out.replace(a, b)
    if fold_accents:
        out = strip_accents(out)
    return out


def tokens(s: str, opts: GradingOptions, *, fold_accents: bool = False) -> list[str]:
    """
    Split a sentence into comparable words.

    Punctuation is dropped rather than made significant: a missing final full
    stop is not a language mistake, and treating it as one fails answers that are
    entirely correct. Hyphens are KEPT inside words -- `segunda-feira` and
    `fim-de-semana` are single words, and splitting them turns one right answer
    into two wrong tokens.
    """
    cleaned = _PUNCT.sub(" ", s or "") if opts.ignore_punctuation else (s or "")
    return normalize(cleaned, opts, fold_accents=fold_accents).split()
