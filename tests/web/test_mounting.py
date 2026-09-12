"""
Repetita mounted on somebody else's application.

The property being tested is that neither arrangement is the special case: it
runs as its own app, and it runs inside a host that owns authentication and
navigation, with the same code path building the library either way.
"""

from pathlib import Path

import pytest
from flask import Flask

from repetita import store
from repetita.store import users
from repetita.web.app import create_app, init_app

COURSE = Path(__file__).resolve().parents[1] / "fixtures" / "demo-course"


def _lib(app):
    """The library for the app's default course. The extension is a shelf now."""
    return app.extensions["repetita"].get(app.config["REPETITA_COURSE_ID"])


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
        # This is the whole reason mounting is worth doing. The comment here
        # used to read "repetita has no authentication of its own"; it has one
        # now, and the sentence that matters survived it intact: inside a host
        # it does not need any, and does not offer any.
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


class TestTheHostOwnsTheShell:
    """
    Accounts exist; the mounting contract did not move.

    `init_app` takes an `identity` callable and nothing else -- no
    `SECRET_KEY`, no session, no login page -- because a host's shell is the
    host's. Every config key there is a `setdefault` for exactly this reason,
    and a `SECRET_KEY` set here would invalidate every session the host had
    already issued.
    """

    def test_a_mounted_repetita_never_asks_for_a_password(self, host, tmp_path):
        con = store.connect(host.config["REPETITA_DB"])
        users.set_password(con, users.DEFAULT_USER, "a-password")
        con.close()
        # Standalone, that password would put a login in the way. Here it must
        # not: the host is already asking whatever it asks.
        assert host.test_client().get("/pt/api/state").status_code == 200

    def test_it_sets_no_secret_key(self, host):
        assert not host.config.get("SECRET_KEY")

    def test_it_names_no_other_accounts_to_the_host(self, host):
        # The switcher cannot work here -- the host decides who you are -- and a
        # control that cannot work should not be offered, nor should somebody
        # else's application be told who has an account in this database.
        con = store.connect(host.config["REPETITA_DB"])
        users.add(con, "karo", password="k")
        con.close()
        body = host.test_client().get("/pt/api/me").get_json()
        assert body["login"] is False
        assert body["accounts"] == []

    def test_the_host_says_who_is_signed_in(self, tmp_path):
        app = Flask(__name__)
        who = {"name": "karo"}
        init_app(
            app,
            COURSE,
            db_path=tmp_path / "study.db",
            url_prefix="/pt",
            identity=lambda: who["name"],
        )
        con = store.connect(app.config["REPETITA_DB"])
        users.add(con, "karo")
        con.close()

        client = app.test_client()
        card = client.get("/pt/api/session").get_json()["cards"][0]
        assert client.post("/pt/api/answer", json={"card_id": card["id"], "text": "x"}).status_code

        con = store.connect(app.config["REPETITA_DB"])
        whose = [r["user_id"] for r in con.execute("SELECT user_id FROM review_log")]
        karo = users.by_name(con, "karo")
        con.close()
        assert whose and set(whose) == {karo.id}, "the host said karo; the row says somebody else"

    def test_a_host_that_says_nothing_gets_the_owner(self, host):
        # Which is exactly what every deployment got before accounts existed.
        con = store.connect(host.config["REPETITA_DB"])
        card = host.test_client().get("/pt/api/session").get_json()["cards"][0]
        host.test_client().post("/pt/api/answer", json={"card_id": card["id"], "text": "x"})
        whose = {r["user_id"] for r in con.execute("SELECT user_id FROM review_log")}
        con.close()
        assert whose == {users.DEFAULT_USER}

    def test_a_name_the_host_invents_is_a_loud_failure(self, tmp_path):
        # Falling back to the owner would file somebody's answers under another
        # person's name, and every one of those rows is a fact about a person's
        # memory.
        app = Flask(__name__)
        # So the exception reaches the test rather than Flask's 500 page. In a
        # real host it is a 500 with a traceback in that host's log, which is
        # the right shape for a configuration mistake in the host's own code.
        app.config["PROPAGATE_EXCEPTIONS"] = True
        init_app(
            app,
            COURSE,
            db_path=tmp_path / "study.db",
            url_prefix="/pt",
            identity=lambda: "nobody-by-that-name",
        )
        with pytest.raises(users.UnknownUser):
            app.test_client().get("/pt/api/state")


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

        a = _lib(standalone)
        b = _lib(hosted)
        assert set(a.cards) == set(b.cards)
        assert a.course.id == b.course.id


class TestTheClientKnowsWhereItIs:
    """
    Root-relative paths in the client work right up until someone mounts the
    app, and then every call 404s at once -- which is what happened the first
    time repetita was mounted in hub. The server is the only thing that knows
    the prefix, so it says.
    """

    def test_standalone_the_base_is_root(self, tmp_path):
        app = create_app(COURSE, db_path=tmp_path / "study.db")
        assert b'data-base="/"' in app.test_client().get("/").data

    def test_mounted_the_base_is_the_prefix(self, host):
        assert b'data-base="/pt/"' in host.test_client().get("/pt/").data

    def test_the_api_lives_under_that_base(self, host):
        client = host.test_client()
        assert client.get("/pt/api/state").status_code == 200
        # And nowhere else -- if this ever answered, the base would not matter
        # and the bug would come back silently.
        assert client.get("/api/state").status_code == 404
