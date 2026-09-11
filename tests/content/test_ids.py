"""
The id-stability check. This is the most consequential check in the repository,
so it is tested in both directions: it must fail, not merely pass.
"""

import subprocess
import textwrap

from repetita.content.ids import ids_at, ids_in

COURSE = """\
format_version: 1
id: c
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


def _course(root, note_ids):
    (root / "courses" / "c").mkdir(parents=True, exist_ok=True)
    (root / "courses" / "c" / "course.yaml").write_text(COURSE)
    d = root / "courses" / "c" / "units" / "01" / "notes"
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"  - id: {i}\n    prompt: Eu ___ hoje.\n    answers: [saio]\n" for i in note_ids
    )
    (d / "n.yaml").write_text(textwrap.dedent("notetype: gap\nnotes:\n") + body)


def test_ids_are_collected_and_namespaced_by_course(tmp_path):
    _course(tmp_path, ["a", "b"])
    assert set(ids_in(tmp_path / "courses")) == {"c/a", "c/b"}


def test_missing_directory_is_an_empty_set_not_an_error(tmp_path):
    assert ids_in(tmp_path / "nope") == {}


class TestAgainstGit:
    def _repo(self, tmp_path, note_ids):
        def run(*a):
            return subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)

        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "t")
        _course(tmp_path, note_ids)
        run("git", "add", "-A")
        run("git", "commit", "-qm", "base")
        return run

    def test_renaming_an_id_is_detected(self, tmp_path, monkeypatch):
        run = self._repo(tmp_path, ["keep", "oldname"])
        monkeypatch.chdir(tmp_path)
        before = ids_at("HEAD")
        assert before == {"c/keep": "c", "c/oldname": "c"}

        # The rename a tidy-minded contributor would make.
        _course(tmp_path, ["keep", "newname"])
        after = ids_in(tmp_path / "courses")

        gone = set(before) - set(after)
        assert gone == {"c/oldname"}, "a renamed id must be reported as gone"
        run("git", "checkout", "-q", "--", ".")

    def test_editing_text_while_keeping_the_id_is_fine(self, tmp_path, monkeypatch):
        self._repo(tmp_path, ["a"])
        monkeypatch.chdir(tmp_path)
        before = ids_at("HEAD")
        path = tmp_path / "courses" / "c" / "units" / "01" / "notes" / "n.yaml"
        path.write_text(path.read_text().replace("Eu ___ hoje.", "Eu ___ agora."))
        assert set(before) - set(ids_in(tmp_path / "courses")) == set()

    def test_absent_courses_at_the_base_ref_is_an_empty_set(self, tmp_path, monkeypatch):
        self._repo(tmp_path, ["a"])
        monkeypatch.chdir(tmp_path)
        assert ids_at("HEAD", "no-such-dir") == {}


class TestRecordedRenames:
    """
    A rename done properly looks identical to a disappearance.

    `repetita rename-id` moves the history across nine tables and writes the
    rename down; `check-ids` reads that record. Without it the command would be
    legal and CI would still reject the pull request, which is the same as not
    having the command.
    """

    def test_a_recorded_rename_is_accepted(self, tmp_path, monkeypatch):
        from repetita.cli import main
        from repetita.content.renames import record

        run = TestAgainstGit()._repo(tmp_path, ["keep", "oldname"])
        monkeypatch.chdir(tmp_path)
        _course(tmp_path, ["keep", "newname"])
        record(tmp_path / "courses" / "c", "oldname", "newname")

        assert main(["check-ids", "--base", "HEAD", "--courses", "courses"]) == 0
        run("git", "checkout", "-q", "--", ".")

    def test_an_unrecorded_rename_is_still_refused(self, tmp_path, monkeypatch):
        from repetita.cli import main

        run = TestAgainstGit()._repo(tmp_path, ["keep", "oldname"])
        monkeypatch.chdir(tmp_path)
        _course(tmp_path, ["keep", "newname"])

        assert main(["check-ids", "--base", "HEAD", "--courses", "courses"]) == 1
        run("git", "checkout", "-q", "--", ".")

    def test_a_record_pointing_at_nothing_is_not_a_rename(self, tmp_path, monkeypatch):
        # A claim, not a rename: the new id has to actually be there.
        from repetita.cli import main
        from repetita.content.renames import record

        run = TestAgainstGit()._repo(tmp_path, ["keep", "oldname"])
        monkeypatch.chdir(tmp_path)
        _course(tmp_path, ["keep"])
        record(tmp_path / "courses" / "c", "oldname", "somewhere-else")

        assert main(["check-ids", "--base", "HEAD", "--courses", "courses"]) == 1
        run("git", "checkout", "-q", "--", ".")

    def test_a_second_rename_keeps_the_chain_readable(self, tmp_path):
        from repetita.content.renames import record, renames

        course = tmp_path / "c"
        course.mkdir()
        record(course, "first", "second")
        record(course, "second", "third")
        # Both the original id and the middle one point at where it ended up,
        # so a check against any base ref finds it.
        assert renames(course) == {"first": "third", "second": "third"}
