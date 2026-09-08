"""
Repetita mounted on somebody else's application.

The property being tested is that neither arrangement is the special case: it
runs as its own app, and it runs inside a host that owns authentication and
navigation, with the same code path building the library either way.
"""

import pytest
from flask import Flask

from repetita.web.app import create_app, init_app

COURSE = "courses/pt-br-from-pl"


@pytest.fixture
def host(tmp_path):
    """A host app standing in for hub: its own routes, its own before_request."""
    app = Flask(__name__)
    seen: list[str] = []

    @app.before_request
    def gate():
        seen.append("gated")

    @app.get("/other/")
    def other():
        return "another tab"

    app.config["seen"] = seen
    init_app(app, COURSE, db_path=tmp_path / "study.db", url_prefix="/pt")
    return app


class TestMounted:
    def test_the_api_answers_under_the_host_prefix(self, host):
        client = host.test_client()
        assert client.get("/pt/api/state").status_code == 200
        assert client.get("/pt/").status_code == 200

    def test_the_host_keeps_its_own_routes(self, host):
        assert host.test_client().get("/other/").data == b"another tab"

    def test_the_host_gate_runs_for_repetita_too(self, host):
        # This is the whole reason mounting is worth doing: repetita has no
        # authentication of its own, and inside a host it does not need any.
        host.test_client().get("/pt/api/state")
        assert "gated" in host.config["seen"]

    def test_static_files_come_from_the_blueprint(self, host):
        response = host.test_client().get("/pt/static/style.css")
        assert response.status_code == 200

    def test_a_session_can_be_answered_through_the_prefix(self, host):
        client = host.test_client()
        card = client.get("/pt/api/session").get_json()["cards"][0]
        result = client.post("/pt/api/answer", json={"card_id": card["id"], "text": "x"})
        assert result.status_code == 200

    def test_the_host_may_add_its_own_navigation(self, host, tmp_path):
        # A template of this name shadows nothing when absent, which is what
        # keeps the standalone page complete.
        assert b"repetita" in host.test_client().get("/pt/").data


class TestStandalone:
    def test_it_is_still_a_whole_application(self, tmp_path):
        app = create_app(COURSE, db_path=tmp_path / "study.db")
        client = app.test_client()
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 200

    def test_both_arrangements_build_the_library_the_same_way(self, tmp_path):
        standalone = create_app(COURSE, db_path=tmp_path / "a.db")
        hosted = Flask(__name__)
        init_app(hosted, COURSE, db_path=tmp_path / "b.db")

        a = standalone.extensions["repetita"]
        b = hosted.extensions["repetita"]
        assert set(a.cards) == set(b.cards)
        assert a.course.id == b.course.id
