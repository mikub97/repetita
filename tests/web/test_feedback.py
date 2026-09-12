"""A comment about the app, saved as a file somebody can commit."""

from __future__ import annotations

import textwrap
from datetime import datetime

import pytest

from repetita.store import feedback
from repetita.web.app import create_app

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    notes:
      - id: a
        l2: um
        l1: jeden
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "checkout" / "courses" / "t"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def client(course_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("REPETITA_USER", "mama")
    monkeypatch.delenv("REPETITA_FEEDBACK", raising=False)
    return create_app(course_dir, db_path=tmp_path / "study.db").test_client()


class TestSaving:
    def test_it_lands_as_a_file_next_to_the_courses(self, client, course_dir):
        response = client.post("/api/feedback", json={"text": "Nie rozumiem.", "where": "study"})

        assert response.status_code == 200
        # Beside `courses/`, not in the working directory -- which for anything
        # double-clicked is wherever the desktop happens to be.
        written = course_dir.parent.parent / "feedback" / "mama"
        files = list(written.glob("*.md"))
        assert len(files) == 1
        assert files[0].name.endswith("-study.md")
        assert response.get_json()["saved"] == files[0].name

    def test_it_records_where_and_which_course(self, client, course_dir):
        client.post("/api/feedback", json={"text": "Coś tu nie gra", "where": "manage"})

        body = next((course_dir.parent.parent / "feedback" / "mama").glob("*.md")).read_text()
        assert "from: mama" in body
        assert "where: manage" in body
        assert "course: t" in body
        assert body.rstrip().endswith("Coś tu nie gra")

    def test_an_empty_comment_is_refused(self, client):
        assert client.post("/api/feedback", json={"text": "   "}).status_code == 400

    def test_two_in_the_same_second_do_not_collide(self, tmp_path, monkeypatch):
        # `REPETITA_FEEDBACK` explicitly: without a course directory `save`
        # falls back to the *working directory*, and a test that writes into
        # the checkout is a test that commits its own output.
        monkeypatch.setenv("REPETITA_FEEDBACK", str(tmp_path / "fb"))
        at = datetime(2026, 9, 14, 19, 32, 11).astimezone()
        one = feedback.save("first", where="study", at=at, user="mama")
        two = feedback.save("second", where="study", at=at, user="mama")

        assert one != two
        assert one.read_text().rstrip().endswith("first")
        assert two.read_text().rstrip().endswith("second")


class TestTheNameIsNotAPath:
    @pytest.mark.parametrize("name", ["../../etc", "/etc/passwd", "..", "", "   "])
    def test_a_name_cannot_escape_the_directory(self, name, tmp_path, monkeypatch):
        # It comes from the environment, so it is not trusted to be a path
        # component. This is the only place a person's name reaches the disk.
        monkeypatch.setenv("REPETITA_FEEDBACK", str(tmp_path / "fb"))

        path = feedback.save("hello", user=name)

        assert path.is_relative_to(tmp_path / "fb")
        assert ".." not in path.parts
