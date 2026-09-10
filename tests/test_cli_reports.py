"""
Finding the file a reported exercise came from.

`Note.origin` is a bare filename, and a unit may keep its notes in a `notes/`
subdirectory or directly in the unit directory -- `loader.py` accepts both, in
that order. So the path has to be probed rather than joined, and this is the part
that would fail silently: a wrong path prints as confidently as a right one, and
the report is only useful because it says where to go.
"""

import textwrap

from repetita.cli import _note_file, _note_line

NOTES = """\
    notetype: vocab
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
    """


def _unit(root, layout: str, name: str = "n.yaml"):
    directory = root / "units" / "01" / ("notes" if layout == "notes" else "")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(textwrap.dedent(NOTES), encoding="utf-8")
    return path


class TestFindingTheFile:
    def test_it_finds_notes_in_a_notes_subdirectory(self, tmp_path):
        written = _unit(tmp_path, "notes")
        assert _note_file(tmp_path, "01", "n.yaml") == written

    def test_it_finds_notes_directly_in_the_unit(self, tmp_path):
        written = _unit(tmp_path, "flat")
        assert _note_file(tmp_path, "01", "n.yaml") == written

    def test_the_notes_subdirectory_wins_as_it_does_in_the_loader(self, tmp_path):
        # `loader.py` reads `notes/*.yaml` or, only if that is empty, `*.yaml`.
        # Resolving in the other order would name a file the loader ignored.
        _unit(tmp_path, "flat")
        nested = _unit(tmp_path, "notes")
        assert _note_file(tmp_path, "01", "n.yaml") == nested

    def test_a_file_that_is_gone_resolves_to_nothing(self, tmp_path):
        # A report outlives the note it describes. Saying nothing beats guessing.
        assert _note_file(tmp_path, "01", "vanished.yaml") is None


class TestFindingTheLine:
    def test_it_points_at_the_note_not_the_file(self, tmp_path):
        path = _unit(tmp_path, "notes")
        assert _note_line(path, "casa") == 3
        assert _note_line(path, "rua") == 6

    def test_a_note_that_is_not_there_has_no_line(self, tmp_path):
        path = _unit(tmp_path, "notes")
        assert _note_line(path, "nowhere") is None
