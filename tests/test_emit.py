"""
Emitting a course directory.

The property that matters is the round trip: what the emitter writes, the loader
must read back unchanged. Note ids especially — they are scheduling keys, and an
id that mutates on the way through here silently orphans the history that was
just imported alongside it.
"""

import datetime as dt

import pytest

from repetita.content.loader import load_course
from repetita.content.models import Course, LanguageSpec, LicenseSpec, Note
from repetita.importers.emit import emit_course


def course(cid="pt-br-from-pl"):
    return Course(
        id=cid,
        l2=LanguageSpec(code="pt", variant="pt-BR"),
        l1=LanguageSpec(code="pl"),
        license=LicenseSpec(name="CC BY-SA 4.0"),
    )


def note(nid, notetype="gap", *, unit="u1", ord=0, lesson=None, tags=(), **fields):
    return Note(
        id=nid,
        notetype=notetype,
        fields=fields,
        tags=tuple(tags),
        lesson=lesson,
        unit=unit,
        ord=ord,
        origin="test",
    )


def roundtrip(tmp_path, notes, c=None):
    emit_course(c or course(), notes, tmp_path / "out")
    return load_course(tmp_path / "out")


class TestRoundTrip:
    def test_what_is_written_loads_back(self, tmp_path):
        notes = [
            note("a", prompt="Eu ___ de casa.", answers=["saio"], cue="sair"),
            note("b", ord=1, prompt="Ela ___ cedo.", answers=["sai"], cue="sair"),
        ]
        r = roundtrip(tmp_path, notes)
        assert r.ok, [str(p) for p in r.problems]
        assert [n.id for n in r.notes] == ["a", "b"]

    def test_note_ids_survive_verbatim(self, tmp_path):
        # Legacy keys look like `pack.item`. If the dot, or anything else, is
        # rewritten here, every imported card_state row is orphaned.
        ids = ["gram-presente.trabalhar-eu", "licao-2026-09-06-vocab.alguem-bateu", "01"]
        notes = [
            note(nid, ord=i, prompt="Eu ___ hoje.", answers=["saio"], cue="c")
            for i, nid in enumerate(ids)
        ]
        r = roundtrip(tmp_path, notes)
        assert [n.id for n in r.notes] == ids

    def test_fields_survive(self, tmp_path):
        n = note(
            "a",
            prompt="Eu ___ de casa às oito.",
            answers=["saio", "saio de casa"],
            cue="sair — presente, 1. os.",
            explain="sair → eu saio, você sai.",
            translation="Wychodzę z domu o ósmej.",
        )
        r = roundtrip(tmp_path, [n])
        got = r.notes[0]
        assert got.fields == n.fields

    def test_the_yaml_boolean_trap_survives_the_round_trip(self, tmp_path):
        # `no` (em + o) is an ordinary Portuguese answer and YAML 1.1 reads it as
        # False. The loader refuses to coerce it, so the emitter has to quote it.
        n = note("a", prompt="Moro ___ Brasil.", answers=["no"], cue="em + o")
        r = roundtrip(tmp_path, [n])
        assert r.ok, [str(p) for p in r.problems]
        assert r.notes[0].answers("answers") == ["no"]

    def test_a_colon_in_a_value_survives(self, tmp_path):
        # An unquoted colon breaks the file outright; the predecessor has a
        # dedicated script for repairing exactly this.
        n = note("a", prompt="Napisz liczbę: 21", answers=["vinte e um"], cue="21")
        r = roundtrip(tmp_path, [n])
        assert r.ok, [str(p) for p in r.problems]
        assert r.notes[0].text("prompt") == "Napisz liczbę: 21"

    def test_accents_are_not_escaped(self, tmp_path):
        n = note("a", prompt="Ele ___ doente.", answers=["está"], cue="stan")
        emit_course(course(), [n], tmp_path / "out")
        written = (tmp_path / "out" / "units" / "u1" / "notes" / "u1.yaml").read_text("utf-8")
        assert "está" in written, "content must stay readable in a diff"

    def test_a_multiline_value_survives(self, tmp_path):
        n = note(
            "a",
            prompt="Eu ___ hoje.",
            answers=["saio"],
            cue="c",
            explain="Pierwsza linia.\nDruga linia.",
        )
        r = roundtrip(tmp_path, [n])
        assert r.notes[0].text("explain") == "Pierwsza linia.\nDruga linia."


class TestStructure:
    def test_one_file_per_unit(self, tmp_path):
        notes = [
            note("a", unit="gram-presente", prompt="x ___", answers=["y"], cue="c"),
            note("b", unit="vocab-comida", prompt="x ___", answers=["y"], cue="c"),
        ]
        emit_course(course(), notes, tmp_path / "out")
        assert (tmp_path / "out" / "units" / "gram-presente" / "notes").is_dir()
        assert (tmp_path / "out" / "units" / "vocab-comida" / "notes").is_dir()

    def test_units_survive_the_round_trip(self, tmp_path):
        notes = [
            note("a", unit="gram-presente", prompt="x ___", answers=["y"], cue="c"),
            note("b", unit="vocab-comida", prompt="x ___", answers=["y"], cue="c"),
        ]
        r = roundtrip(tmp_path, notes)
        assert {n.id: n.unit for n in r.notes} == {"a": "gram-presente", "b": "vocab-comida"}

    def test_a_shared_notetype_is_hoisted_to_the_file(self, tmp_path):
        notes = [
            note(
                f"n{i}",
                "transform",
                ord=i,
                prompt="Eu compro.",
                instruction="no plural",
                answers=["Nós compramos."],
            )
            for i in range(3)
        ]
        emit_course(course(), notes, tmp_path / "out")
        text = (tmp_path / "out" / "units" / "u1" / "notes" / "u1.yaml").read_text("utf-8")
        assert text.count("notetype: transform") == 1, "declared once, not per note"
        assert roundtrip(tmp_path, notes).notes[0].notetype == "transform"

    def test_a_mixed_file_keeps_the_notetype_on_each_note(self, tmp_path):
        notes = [
            note("a", "gap", prompt="Eu ___ hoje.", answers=["saio"], cue="c"),
            note("b", "sentence", ord=1, prompt="Wychodzę.", answers=["Eu saio de casa."]),
        ]
        r = roundtrip(tmp_path, notes)
        assert {n.id: n.notetype for n in r.notes} == {"a": "gap", "b": "sentence"}

    def test_lesson_dates_survive(self, tmp_path):
        day = dt.date(2026, 9, 6)
        notes = [note("a", lesson=day, prompt="x ___", answers=["y"], cue="c")]
        assert roundtrip(tmp_path, notes).notes[0].lesson == day

    def test_tags_survive_whether_shared_or_not(self, tmp_path):
        notes = [
            note("a", tags=("vocabulario", "A2"), prompt="x ___", answers=["y"], cue="c"),
            note("b", ord=1, tags=("vocabulario", "B1"), prompt="x ___", answers=["y"], cue="c"),
        ]
        r = roundtrip(tmp_path, notes)
        got = {n.id: set(n.tags) for n in r.notes}
        assert got == {"a": {"vocabulario", "A2"}, "b": {"vocabulario", "B1"}}

    def test_notes_keep_their_order(self, tmp_path):
        notes = [note(f"n{i}", ord=i, prompt="x ___", answers=["y"], cue="c") for i in range(5)]
        # Emitted out of order on purpose; `ord` is what decides.
        r = roundtrip(tmp_path, list(reversed(notes)))
        assert [n.id for n in r.notes] == [f"n{i}" for i in range(5)]

    def test_emitting_twice_gives_the_same_bytes(self, tmp_path):
        # A regenerated course must produce an empty diff, or it is unreviewable.
        notes = [note("a", prompt="x ___", answers=["y"], cue="c")]
        emit_course(course(), notes, tmp_path / "out")
        first = (tmp_path / "out" / "units" / "u1" / "notes" / "u1.yaml").read_bytes()
        emit_course(course(), notes, tmp_path / "out")
        assert (tmp_path / "out" / "units" / "u1" / "notes" / "u1.yaml").read_bytes() == first


class TestCourseFile:
    def test_grading_settings_survive(self, tmp_path):
        r = roundtrip(tmp_path, [note("a", prompt="x ___", answers=["y"], cue="c")])
        assert r.course.grading.fold_accents is True
        assert r.course.l2.variant == "pt-BR"

    @pytest.mark.parametrize("cid", ["pt-br-from-pl", "pt-br-from-pl-private"])
    def test_the_course_id_is_preserved(self, tmp_path, cid):
        r = roundtrip(tmp_path, [note("a", prompt="x ___", answers=["y"], cue="c")], course(cid))
        assert r.course.id == cid
