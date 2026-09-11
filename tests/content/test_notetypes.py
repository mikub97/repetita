"""
Exercise types as a registry, and a course that declares its own.

The type was the one extension point that was not a file plus a registry entry:
six of them in a single Python dict, so adding one meant editing the engine. The
test that matters here is `test_a_course_can_declare_its_own_type` -- and after
it, the four ways a declaration can be wrong, because nothing checked any of them
while the six were literals a person had typed carefully.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita.content.loader import load_course
from repetita.content.notetypes import builtin, declared, get, names, problems_with

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

DITADO = """\
    ditado:
      fields:
        audio:   {type: audio, required: true, visibility: before}
        answers: {type: text_list, required: true, visibility: after}
        hint:    {type: text}
      cards:
        write:   {ask: [audio], expect: answers, grader: typed, forms: [typein]}
    """


def course(tmp_path, *, types: str | None = None, notes: str = "") -> object:
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    if types:
        (root / "notetypes.yaml").write_text(textwrap.dedent(types))
    if notes:
        (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(notes))
    return root


def a_type(**cards):
    return {
        "fields": {
            "q": {"visibility": "before"},
            "a": {"type": "text_list", "visibility": "after"},
        },
        "cards": cards
        or {"c": {"ask": ["q"], "expect": "a", "grader": "typed", "forms": ["typein"]}},
    }


class TestTheRegistry:
    def test_the_six_are_there(self):
        assert names() == ["gap", "phrase", "picture", "sentence", "transform", "vocab"]

    def test_an_unknown_one_says_what_there_is(self):
        with pytest.raises(LookupError, match="available: gap"):
            get("quiz")

    def test_every_built_in_passes_the_checks_it_imposes_on_courses(self):
        # It did not, when the checks were first written: `phrase` declared a
        # word bank its `self` grader cannot judge, and `vocab.recognize` a
        # flashcard its `typed` grader cannot judge. Both were unreachable behind
        # an earlier form, so nobody had ever met them.
        assert {
            n: [p.detail for p in problems_with(t)]
            for n, t in builtin().items()
            if problems_with(t)
        } == {}

    def test_builtin_hands_out_a_copy(self):
        # A course adds to it; the registry must not learn from that.
        builtin()["invented"] = get("gap")
        assert "invented" not in builtin()


class TestACourseDeclaringItsOwn:
    def test_a_course_can_declare_its_own_type(self, tmp_path):
        # The whole point: a seventh type, with no Python touched.
        root = course(
            tmp_path,
            types=DITADO,
            notes="""
                notetype: ditado
                notes:
                  - id: d1
                    audio: media/um.mp3
                    answers: [Bom dia]
            """,
        )
        result = load_course(root)
        assert result.ok
        assert "ditado" in result.notetypes
        assert [c.id for c in result.cards] == ["d1#write"]
        assert result.cards[0].grader == "typed"

    def test_no_file_is_the_ordinary_case(self, tmp_path):
        result = load_course(course(tmp_path))
        assert sorted(result.notetypes) == names()
        assert not result.problems

    def test_a_course_type_can_shadow_a_built_in(self, tmp_path):
        # Deliberate: a course that wants `gap` to behave differently for its own
        # material should not have to invent a name to do it.
        result = load_course(
            tmp_path_course := course(
                tmp_path,
                types="gap:\n"
                + textwrap.indent(
                    textwrap.dedent("""\
                fields:
                  prompt: {required: true}
                  answers: {type: text_list, required: true, visibility: after}
                cards:
                  fill: {ask: [prompt], expect: answers, grader: sentence, forms: [typein]}
                """),
                    "  ",
                ),
            )
        )
        assert tmp_path_course
        assert result.notetypes["gap"].cards["fill"].grader == "sentence"


class TestARefusedDeclaration:
    @pytest.mark.parametrize(
        "label, cards, expected",
        [
            (
                "unknown grader",
                {"c": {"ask": ["q"], "expect": "a", "grader": "psychic", "forms": ["typein"]}},
                "names grader",
            ),
            (
                "ask names nothing",
                {"c": {"ask": ["nope"], "expect": "a", "grader": "typed", "forms": ["typein"]}},
                "does not have",
            ),
            (
                "expect names nothing",
                {"c": {"ask": ["q"], "expect": "nope", "grader": "typed", "forms": ["typein"]}},
                "not a field",
            ),
            (
                "ungradeable form",
                {"c": {"ask": ["q"], "expect": "a", "grader": "typed", "forms": ["flashcard"]}},
                "cannot judge",
            ),
            (
                "no forms",
                {"c": {"ask": ["q"], "expect": "a", "grader": "typed", "forms": []}},
                "no forms",
            ),
        ],
    )
    def test_it_is_named_rather_than_accepted(self, label, cards, expected):
        types, problems = declared({"mine": a_type(**cards)})
        assert types == {}, f"{label} should not have been accepted"
        assert any(expected in p.detail for p in problems), [p.detail for p in problems]

    def test_a_type_with_no_cards_at_all(self):
        types, problems = declared({"mine": {"fields": {"q": {}}, "cards": {}}})
        assert types == {}
        assert any("no cards" in p.detail for p in problems)

    def test_a_broken_one_does_not_take_the_good_ones_with_it(self):
        types, problems = declared(
            {
                "fine": a_type(),
                "broken": a_type(
                    c={"ask": ["q"], "expect": "a", "grader": "psychic", "forms": ["typein"]}
                ),
            }
        )
        assert set(types) == {"fine"}
        assert problems

    def test_a_course_with_a_broken_type_still_loads_the_rest(self, tmp_path):
        result = load_course(
            course(
                tmp_path,
                types="""
                    mine:
                      fields:
                        q: {}
                      cards:
                        c: {ask: [q], expect: q, grader: psychic, forms: [typein]}
                """,
            )
        )
        assert "mine" not in result.notetypes
        assert sorted(result.notetypes) == names(), "the built-ins are untouched"
        assert result.problems
