"""
Naming an exercise in a few characters.

The rule is one line of code and two measurements, and the measurements are the
part worth pinning: the answer beats the cue head, and a name is allowed to
repeat. These tests say both out loud, because the second one reads like a bug
to anyone who has not read ADR-0008 or `labels.py`.
"""

from __future__ import annotations

from repetita.content.labels import MAX, derive, shorten
from repetita.content.models import (
    CardTemplate,
    FacetAxis,
    Facets,
    FamilySpec,
    FieldSpec,
    Note,
    NoteType,
)

VOCAB = NoteType(
    name="vocab",
    fields={
        "l1": FieldSpec(type="text", visibility="before"),
        "l2": FieldSpec(type="text", visibility="after"),
    },
    cards={"produce": CardTemplate(ask=("l1",), expect="l2", grader="typed", forms=("type",))},
)

FACETS = Facets(axes={"topic": FacetAxis(catch_all=True)}, family=FamilySpec(field="l1"))


def note(**kw) -> Note:
    base = {
        "id": "u.one",
        "notetype": "vocab",
        "unit": "u",
        "fields": {"l1": "targ", "l2": "a feira"},
    }
    return Note(**{**base, **kw})


class TestWhatItPicks:
    def test_the_answer_is_the_name(self):
        # Measured over 757 real notes: median 6 characters against the cue
        # head's 10, and half the collisions. A cue head names a family.
        assert derive(note(), VOCAB) == "a feira"

    def test_a_sentence_is_cut_at_a_word(self):
        long = note(fields={"l1": "x", "l2": "Nós trabalhamos no centro da cidade"})
        assert derive(long, VOCAB) == "Nós trabalhamos no…"
        assert len(derive(long, VOCAB)) <= MAX

    def test_it_falls_back_to_the_family_when_there_is_no_answer(self):
        empty = note(fields={"l1": "morar — imperfeito, eu", "l2": ""})
        assert derive(empty, VOCAB, FACETS) == "morar"

    def test_it_falls_back_to_the_id_when_the_type_is_gone(self):
        # A course that no longer declares the type still has rows on the board,
        # and a row with no name is worse than one named awkwardly.
        assert derive(note(id="lekcja-11.morar-eu"), None) == "morar-eu"


class TestShortening:
    def test_it_leaves_what_already_fits(self):
        assert shorten("atrás") == "atrás"

    def test_it_backs_up_to_a_word_boundary(self):
        assert shorten("depois de amanhã cedo") == "depois de amanhã…"

    def test_it_cuts_mid_word_rather_than_returning_almost_nothing(self):
        # Backing up would leave "a…", which names nothing at all.
        assert shorten("a supercalifragilistic") == "a supercalifragilis…"

    def test_it_does_not_end_on_punctuation_before_the_ellipsis(self):
        assert shorten("Bom dia a todos, como vai?") == "Bom dia a todos…"


class TestNamesAreNotIdentifiers:
    def test_two_exercises_may_share_a_name(self):
        # Fifteen exercises in one set legitimately answer "o". Making the name
        # unique would mean inventing text nobody wrote; the board disambiguates
        # with the id suffix where it actually matters. See docs/labels.md.
        a = note(id="genero.cinema", fields={"l1": "kino", "l2": "o"})
        b = note(id="genero.carro", fields={"l1": "samochód", "l2": "o"})
        assert derive(a, VOCAB) == derive(b, VOCAB) == "o"
