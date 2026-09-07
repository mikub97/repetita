import textwrap

import pytest

from repetita.content.loader import load_course
from repetita.content.notetypes import BUILTIN

COURSE = """\
format_version: 1
id: test-course
l2: {code: pt, variant: pt-BR}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


@pytest.fixture
def course(tmp_path):
    def build(notes_yaml: str, course_yaml: str = COURSE):
        (tmp_path / "course.yaml").write_text(course_yaml)
        d = tmp_path / "units" / "01" / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (d / "notes.yaml").write_text(textwrap.dedent(notes_yaml))
        return load_course(tmp_path)

    return build


class TestCourse:
    def test_a_valid_course_loads(self, course):
        r = course("""\
            notetype: gap
            notes:
              - id: a
                prompt: Eu ___ de casa.
                answers: [saio]
            """)
        assert r.ok
        assert r.course is not None
        assert r.course.l2.variant == "pt-BR"
        assert [n.id for n in r.notes] == ["a"]

    def test_unsupported_format_version_is_refused_by_name(self, course):
        r = course("notes: []", COURSE.replace("format_version: 1", "format_version: 99"))
        assert not r.ok
        assert "format_version" in str(r.fatal[0])

    def test_missing_course_file_is_reported_not_raised(self, tmp_path):
        r = load_course(tmp_path)
        assert not r.ok
        assert "course.yaml" in str(r.fatal[0])


class TestNotes:
    def test_unknown_notetype_names_the_note_and_the_alternatives(self, course):
        r = course("""\
            notes:
              - id: oops
                notetype: nosuchtype
                prompt: x
            """)
        problem = r.fatal[0]
        assert problem.note_id == "oops"
        assert "nosuchtype" in problem.detail
        assert "gap" in problem.detail, "the message must say what IS available"
        assert r.notes == []

    def test_malformed_lesson_date_is_refused_not_ignored(self, course):
        # Silently dropping it would push the file to the back of the
        # introduction order, which looks exactly like the app ignoring today's
        # lesson -- a far more confusing failure than an error.
        r = course("""\
            lesson: wczoraj
            notes:
              - id: a
                prompt: x
                answers: [y]
            """)
        assert not r.ok
        assert "YYYY-MM-DD" in r.fatal[0].detail
        assert r.notes == []

    def test_a_valid_lesson_date_is_kept(self, course):
        r = course("""\
            lesson: 2026-09-06
            notes:
              - id: a
                prompt: x
                answers: [y]
            """)
        assert r.notes[0].lesson.isoformat() == "2026-09-06"

    def test_duplicate_ids_are_refused(self, course):
        r = course("""\
            notes:
              - id: same
                prompt: x
                answers: [y]
              - id: same
                prompt: z
                answers: [w]
            """)
        assert any(p.kind == "duplicate" for p in r.fatal)
        assert len(r.notes) == 1, "the first wins; the second must not silently overwrite it"

    def test_yaml_boolean_answers_are_refused_with_the_fix(self, course):
        # `no` (em + o) is an ordinary Portuguese answer, and YAML 1.1 reads it
        # as False. Coercing it back would create a note whose correct answer is
        # the text "False".
        r = course("""\
            notes:
              - id: a
                prompt: Moro ___ Brasil.
                answers: [no]
            """)
        assert not r.ok
        detail = r.fatal[0].detail
        assert "bool" in detail and "quote it" in detail

    def test_quoting_it_makes_it_load(self, course):
        r = course("""\
            notes:
              - id: a
                prompt: Moro ___ Brasil.
                answers: ["no"]
            """)
        assert r.ok
        assert r.notes[0].answers("answers") == ["no"]

    def test_unknown_field_is_refused(self, course):
        r = course("""\
            notes:
              - id: a
                prompt: x
                answers: [y]
                cuee: literówka
            """)
        assert "cuee" in r.fatal[0].detail

    def test_missing_required_field_is_refused(self, course):
        r = course("""\
            notes:
              - id: a
                cue: bez promptu
            """)
        assert any("requires" in p.detail for p in r.fatal)

    def test_broken_yaml_points_at_the_usual_cause(self, course):
        r = course("""\
            notes:
              - id: a
                prompt: to: rozwala plik
            """)
        assert not r.ok
        assert "quote" in r.fatal[0].detail


class TestCardExpansion:
    def test_one_vocab_note_becomes_two_cards_without_audio(self, course):
        r = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
            """)
        assert sorted(c.template for c in r.cards) == ["produce", "recognize"]
        assert all(c.note_id == "casa" for c in r.cards)

    def test_adding_audio_adds_the_listening_card_with_no_other_change(self, course):
        r = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
                audio: media/casa.mp3
            """)
        assert sorted(c.template for c in r.cards) == ["listen", "produce", "recognize"]

    def test_card_ids_are_derived_from_the_note_id(self, course):
        r = course("""\
            notetype: vocab
            notes:
              - id: casa
                l2: a casa
                l1: dom
            """)
        assert {c.id for c in r.cards} == {"casa#recognize", "casa#produce"}

    def test_cards_carry_their_grader_and_forms(self, course):
        r = course("""\
            notes:
              - id: a
                prompt: Eu ___ de casa.
                answers: [saio]
            """)
        card = r.cards[0]
        assert card.grader == "typed"
        assert "typein" in card.forms


class TestNoteTypes:
    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_every_field_declares_when_it_is_visible(self, name):
        # The anti-leak guarantee rests on this split being total. A field with
        # no declared visibility would be a hole in it.
        nt = BUILTIN[name]
        assert all(f.visibility in ("before", "after") for f in nt.fields.values())

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_every_template_expects_a_declared_field(self, name):
        nt = BUILTIN[name]
        for tpl in nt.cards.values():
            assert tpl.expect in nt.fields
            assert all(a in nt.fields for a in tpl.ask)
            assert all(r in nt.fields for r in tpl.requires)

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_the_expected_answer_is_never_visible_before_answering(self, name):
        # The invariant is per card, not per field: in `vocab`, `l1` is the
        # prompt for `produce` and the answer for `recognize`. The field spec
        # cannot express that on its own, so the card's answer is excluded
        # unconditionally.
        nt = BUILTIN[name]
        for tname, tpl in nt.cards.items():
            assert tpl.expect not in nt.visible_before(tname), (
                f"{name}.{tname} would show its own answer"
            )

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_the_prompt_fields_of_a_card_are_shown(self, name):
        nt = BUILTIN[name]
        for tname, tpl in nt.cards.items():
            shown = nt.visible_before(tname)
            asked = [a for a in tpl.ask if a != tpl.expect]
            assert all(a in shown for a in asked), f"{name}.{tname} hides its own question"
