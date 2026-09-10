"""
Reading a flat tag bag as answers to questions.

The classifier is a pure function over strings, so these are unit tests. What
they mostly pin is the *policy*: which axis claims a tag, and -- more important
than it looks -- that a badly filed note is never quarantined.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repetita.content.facets import axis_of, classify, resolve
from repetita.content.models import FacetAxis, Facets

FACETS = Facets(
    axes={
        "level": FacetAxis(values=("A1", "A2", "B1"), ordered=True, max_per_note=1),
        "track": FacetAxis(values=("vocabulario", "gramatica", "musica", "fala")),
        "topic": FacetAxis(catch_all=True),
    },
    aliases={"tempo-adverbios": "tempo"},
)


class TestClassifying:
    def test_a_flat_bag_becomes_answers_to_questions(self):
        placed, problems = classify(("A2", "gramatica", "preposicoes"), FACETS)
        assert placed == {
            "level": ("A2",),
            "track": ("gramatica",),
            "topic": ("preposicoes",),
        }
        assert problems == []

    def test_a_note_may_hold_several_values_on_one_axis(self):
        # The ordinary case, not an edge one: material about ordering food in a
        # market really is both, and forcing a choice loses that.
        placed, problems = classify(("comida", "cidade"), FACETS)
        assert placed["topic"] == ("comida", "cidade")
        assert problems == []

    def test_an_unclaimed_tag_falls_to_the_catch_all(self):
        # This is what makes adding a topic an edit to a note rather than to the
        # course's configuration.
        placed, _ = classify(("baianismos",), FACETS)
        assert placed == {"topic": ("baianismos",)}

    def test_a_declared_value_beats_the_catch_all(self):
        # So promoting a value onto a real axis reclassifies it everywhere with
        # no note touched.
        placed, _ = classify(("musica",), FACETS)
        assert placed == {"track": ("musica",)}

    def test_an_alias_keeps_older_material_resolving(self):
        placed, _ = classify(("tempo-adverbios",), FACETS)
        assert placed == {"topic": ("tempo",)}
        assert resolve("tempo-adverbios", FACETS) == "tempo"

    def test_a_duplicate_tag_is_recorded_once(self):
        placed, _ = classify(("comida", "comida"), FACETS)
        assert placed["topic"] == ("comida",)


class TestProblems:
    def test_too_many_values_on_a_capped_axis_is_reported(self):
        _, problems = classify(("A1", "A2"), FACETS)
        assert [p.kind for p in problems] == ["taxonomy"]
        assert "level allows 1" in problems[0].detail

    def test_a_taxonomy_problem_never_quarantines_the_note(self):
        # The rule that matters. A note filed under nothing is badly filed, not
        # broken, and taking working material away from a learner over a
        # bookkeeping mistake is the opposite of what quarantine is for.
        _, problems = classify(("A1", "A2"), FACETS)
        assert all(not p.fatal for p in problems)

    def test_an_unplaceable_tag_is_reported_when_there_is_no_catch_all(self):
        strict = Facets(axes={"level": FacetAxis(values=("A1",))})
        placed, problems = classify(("A1", "mystery"), strict)
        assert placed == {"level": ("A1",)}
        assert problems and not problems[0].fatal
        assert "mystery" in problems[0].detail

    def test_two_catch_all_axes_are_refused(self):
        # Two would make classification depend on dict order, which is a coin
        # toss dressed up as configuration.
        with pytest.raises(ValidationError):
            Facets(
                axes={
                    "a": FacetAxis(catch_all=True),
                    "b": FacetAxis(catch_all=True),
                }
            )


class TestNoFacets:
    def test_a_course_without_facets_classifies_nothing_and_complains_about_nothing(self):
        # Every course written before this existed is still valid, and a course
        # that has not decided how to sort its material is not a broken one.
        placed, problems = classify(("A2", "gramatica"), Facets())
        assert placed == {}
        assert problems and not problems[0].fatal

    def test_axis_of_returns_none_when_nothing_claims_a_tag(self):
        assert axis_of("whatever", Facets()) is None
