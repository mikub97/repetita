"""
Re-reading the course without a restart.

This is what makes "tonight's lesson, in tonight's queue" possible. It is also
the endpoint most able to do harm: it replaces the material a session is being
built from, while a session is being used.
"""

import textwrap

import pytest

from repetita import store
from repetita.web.app import create_app

COURSE_YAML = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

ONE = """\
    notetype: gap
    notes:
      - id: a
        prompt: Eu ___ de casa.
        answers: [saio]
        cue: sair
    """

TWO = """\
    notetype: gap
    notes:
      - id: a
        prompt: Eu ___ de casa.
        answers: [saio]
        cue: sair
      - id: b
        prompt: Ela ___ cedo.
        answers: [sai]
        cue: sair
    """


@pytest.fixture
def course(tmp_path):
    root = tmp_path / "course"
    notes = root / "units" / "01" / "notes"
    notes.mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE_YAML)
    (notes / "n.yaml").write_text(textwrap.dedent(ONE))
    return root


@pytest.fixture
def app(course, tmp_path):
    return create_app(course, db_path=tmp_path / "study.db")


def write(course, body):
    (course / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(body))


class TestReload:
    def test_new_material_arrives_without_a_restart(self, app, course):
        client = app.test_client()
        assert client.get("/api/state").get_json()["cards"] == 1

        write(course, TWO)
        result = client.post("/api/reload").get_json()

        assert result["cards"] == 2
        assert result["added_total"] == 1
        assert client.get("/api/state").get_json()["cards"] == 2

    def test_it_says_what_changed_not_just_that_it_ran(self, app, course):
        # A reload that silently loaded nothing looks identical to one that
        # worked, which is how "the app is ignoring today's lesson" starts.
        client = app.test_client()
        write(course, TWO)
        result = client.post("/api/reload").get_json()
        assert result["added"] == ["b#fill"]
        assert result["removed"] == []

    def test_a_reload_that_changes_nothing_says_so(self, app):
        result = app.test_client().post("/api/reload").get_json()
        assert result["added_total"] == 0 and result["removed_total"] == 0

    def test_a_broken_course_leaves_the_running_one_in_place(self, app, course):
        # Swapping in a half-loaded library and reporting the error afterwards
        # would take the learner's material away over a typo in a file they were
        # in the middle of editing.
        client = app.test_client()
        (course / "course.yaml").write_text("format_version: 99\nid: t\n")

        response = client.post("/api/reload")

        assert response.status_code == 422
        assert client.get("/api/state").get_json()["cards"] == 1

    def test_progress_survives_a_reload(self, app, course, tmp_path):
        client = app.test_client()
        card = client.get("/api/session").get_json()["cards"][0]
        client.post("/api/answer", json={"card_id": card["id"], "text": "saio"})

        write(course, TWO)
        client.post("/api/reload")

        con = store.connect(tmp_path / "study.db")
        try:
            assert len(store.all_states(con)) == 1
            assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == 1
        finally:
            con.close()

    def test_a_handle_survives_a_reload(self, app, course):
        # Otherwise every open question would break whenever material was added,
        # which is precisely when someone is most likely to be studying.
        client = app.test_client()
        card = client.get("/api/session").get_json()["cards"][0]

        write(course, TWO)
        client.post("/api/reload")

        assert (
            client.post("/api/answer", json={"card_id": card["id"], "text": "saio"}).status_code
            == 200
        )

    def test_removed_material_is_reported(self, app, course):
        client = app.test_client()
        write(course, TWO)
        client.post("/api/reload")
        write(course, ONE)

        result = client.post("/api/reload").get_json()

        assert result["removed"] == ["b#fill"]
        assert result["cards"] == 1
