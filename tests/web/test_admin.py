"""
The admin page.

A generic browser over 25 tables, and the tests that matter are about the tables
it refuses to write to. A text field over `review_log` in a browser is how a
month of study disappears at one in the morning, and the point of this feature
is that it cannot.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.store import users as U
from repetita.web.app import create_app

COURSE = """\
format_version: 1
id: t
l2: {code: it}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    tags: [A1]
    notes:
      - id: uno
        l2: uno
        l1: jeden
    """


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "c"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    made = create_app(root, db_path=tmp_path / "study.db")
    con = store.connect(made.config["REPETITA_DB"])
    U.rename(con, U.DEFAULT_USER, "mikub")
    U.set_password(con, "mikub", "m-pass")
    U.add(con, "karo", password="k-pass")
    con.close()
    return made


@pytest.fixture
def con(app):
    c = store.connect(app.config["REPETITA_DB"])
    yield c
    c.close()


def as_(app, name, password):
    client = app.test_client()
    assert client.post("/api/login", json={"name": name, "password": password}).status_code == 200
    return client


@pytest.fixture
def admin(app):
    return as_(app, "mikub", "m-pass")


class TestOnlyAnAdmin:
    def test_an_ordinary_account_is_refused(self, app):
        client = as_(app, "karo", "k-pass")
        assert client.get("/api/admin/tables").status_code == 403

    def test_every_admin_route_is_gated(self, app):
        # One missed call to `_must_be_admin` is the whole of the hole, so this
        # asserts over the routes the app actually has rather than a list
        # written by hand -- a route added later is covered the day it is added.
        client = as_(app, "karo", "k-pass")
        rules = [r for r in app.url_map.iter_rules() if "/api/admin/" in str(r.rule)]
        assert len(rules) >= 5, f"only found {len(rules)} admin routes; is the sweep working?"
        checked = 0
        for rule in rules:
            # The parameterised ones matter most: `<name>` is where a table goes,
            # and skipping them would leave the three that read and write rows
            # untested. `users` is a real table, so a 403 here is the gate and
            # not a 404 on the way to it.
            path = str(rule.rule).replace("<name>", "users")
            assert "<" not in path, f"unhandled parameter in {rule.rule}"
            for method in sorted(rule.methods & {"GET", "POST", "PATCH", "DELETE"}):
                answer = client.open(path, method=method, json={})
                assert answer.status_code == 403, (
                    f"{method} {path} answered {answer.status_code}, not 403"
                )
                checked += 1
        assert checked >= 7, f"only {checked} method/route pairs checked"

    def test_signed_out_is_refused_before_it_is_even_asked(self, app):
        assert app.test_client().get("/api/admin/tables").status_code == 401

    def test_an_admin_gets_in(self, admin):
        body = admin.get("/api/admin/tables").get_json()
        assert {t["name"] for t in body["tables"]} >= {"users", "notes", "review_log"}


class TestWhatItWillNotWriteTo:
    def key_row(self, con):
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,seen) "
            "VALUES(1,'uno#recognize','sm2',1,'{}',3)"
        )
        con.execute(
            "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo,mode) "
            "VALUES(1,'uno#recognize',3,'2026-09-12T10:00:00Z','2026-09-12','sm2','session')"
        )
        con.commit()

    @pytest.mark.parametrize("table", ["review_log", "card_state", "plan_revisions"])
    def test_history_is_read_only(self, admin, con, table):
        # Rule 1, as a property of the newest screen in the app rather than a
        # promise about it.
        self.key_row(con)
        listed = next(
            t for t in admin.get("/api/admin/tables").get_json()["tables"] if t["name"] == table
        )
        assert listed["editable"] is False
        assert listed["frozen"] == "irreplaceable", "a read-only table owes a reason"

    def test_it_refuses_to_delete_an_answer(self, admin, con):
        self.key_row(con)
        answer = admin.delete("/api/admin/tables/review_log", json={"key": {"id": 1}})
        assert answer.status_code == 400
        assert con.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"] == 1

    def test_it_refuses_to_edit_a_schedule(self, admin, con):
        self.key_row(con)
        answer = admin.patch(
            "/api/admin/tables/card_state",
            json={"key": {"user_id": 1, "card_id": "uno#recognize"}, "values": {"seen": 999}},
        )
        assert answer.status_code == 400
        assert con.execute("SELECT seen FROM card_state").fetchone()["seen"] == 3

    @pytest.mark.parametrize("table", ["notes", "cards", "units", "note_facets"])
    def test_material_is_edited_where_material_is_edited(self, admin, table):
        listed = next(
            t for t in admin.get("/api/admin/tables").get_json()["tables"] if t["name"] == table
        )
        assert listed["editable"] is False
        assert listed["frozen"] == "owned_elsewhere"
        # And says where to go. A refusal that does not is a dead end.
        assert listed["instead"] == "manage", f"{table} does not say where to edit it"

    def test_the_refusal_is_a_code_and_not_a_sentence(self, admin):
        # UI text is translated and the engine ships none of it as a Python
        # literal (CLAUDE.md). The client turns this into the same sentence it
        # already shows in the banner over the table.
        answer = admin.patch(
            "/api/admin/tables/notes",
            json={"key": {"id": "uno"}, "values": {"tags": "[]"}},
        )
        assert answer.status_code == 400
        assert answer.get_json()["error"] == "owned_elsewhere:notes"


class TestWhatItWill:
    def test_it_reads_a_page_of_rows(self, admin):
        body = admin.get("/api/admin/tables/users").get_json()
        assert {r["name"] for r in body["rows"]} == {"mikub", "karo"}
        assert body["total"] == 2

    def test_it_never_returns_a_password_hash_through_the_account_form(self, admin):
        body = admin.get("/api/admin/accounts").get_data(as_text=True)
        assert "password_hash" not in body
        assert "scrypt" not in body

    def test_it_edits_a_table_that_owns_itself(self, admin, con):
        assert (
            admin.patch(
                "/api/admin/tables/users",
                json={"key": {"id": 2}, "values": {"display": "Karolina"}},
            ).status_code
            == 200
        )
        assert con.execute("SELECT display FROM users WHERE id = 2").fetchone()["display"] == (
            "Karolina"
        )

    def test_a_filter_narrows_by_an_exact_match(self, admin):
        body = admin.get("/api/admin/tables/users?f.name=karo").get_json()
        assert [r["name"] for r in body["rows"]] == ["karo"]

    def test_a_filter_on_a_column_that_does_not_exist_shows_the_table(self, admin):
        # A stale filter in a URL should show the table, not an error page.
        body = admin.get("/api/admin/tables/users?f.nonsense=x").get_json()
        assert body["total"] == 2

    def test_a_partial_primary_key_is_refused(self, admin, con):
        # `DELETE FROM card_state WHERE card_id = ?` without the user_id is one
        # keystroke from deleting every account's row for that card. Asserted on
        # an editable table, so it is the key check being tested and not the
        # read-only one.
        con.execute("INSERT INTO enrolments(user_id, course) VALUES(1,'t'),(2,'t')")
        con.commit()
        answer = admin.delete("/api/admin/tables/enrolments", json={"key": {"course": "t"}})
        assert answer.status_code == 400
        assert con.execute("SELECT COUNT(*) AS n FROM enrolments").fetchone()["n"] == 2

    def test_a_whole_primary_key_deletes_exactly_one_row(self, admin, con):
        con.execute("INSERT INTO enrolments(user_id, course) VALUES(1,'t'),(2,'t')")
        con.commit()
        answer = admin.delete(
            "/api/admin/tables/enrolments", json={"key": {"user_id": 1, "course": "t"}}
        )
        assert answer.status_code == 200
        left = [r["user_id"] for r in con.execute("SELECT user_id FROM enrolments")]
        assert left == [2]

    def test_a_primary_key_is_not_edited_in_place(self, admin):
        answer = admin.patch(
            "/api/admin/tables/users", json={"key": {"id": 2}, "values": {"id": 99}}
        )
        assert answer.status_code == 400

    def test_an_unknown_table_is_a_404(self, admin):
        assert admin.get("/api/admin/tables/robert'); DROP TABLE users;--").status_code == 404


class TestTheAccountForm:
    def test_it_adds_an_account(self, admin, con):
        assert (
            admin.post(
                "/api/admin/accounts",
                json={"verb": "add", "name": "rzadki", "display": "Radek", "password": "r"},
            ).status_code
            == 200
        )
        assert U.authenticate(con, "rzadki", "r") is not None

    def test_it_deactivates_rather_than_deletes(self, admin, con):
        admin.post("/api/admin/accounts", json={"verb": "active", "name": "karo", "active": False})
        karo = U.by_name(con, "karo")
        assert karo is not None and not karo.active

    def test_a_rename_carries_the_sets(self, admin, con):
        con.execute("UPDATE units SET owner = 'karo' WHERE id = '01'")
        con.commit()
        admin.post("/api/admin/accounts", json={"verb": "rename", "name": "karo", "to": "karola"})
        assert con.execute("SELECT owner FROM units WHERE id = '01'").fetchone()["owner"] == (
            "karola"
        )

    def test_it_enrols_and_unenrols(self, admin, con):
        admin.post("/api/admin/accounts", json={"verb": "enrol", "name": "karo", "course": "t"})
        karo = U.by_name(con, "karo")
        assert U.enrolments(con, karo.id) == ["t"]
        admin.post("/api/admin/accounts", json={"verb": "leave", "name": "karo", "course": "t"})
        assert U.enrolments(con, karo.id) == []

    def test_an_unknown_verb_is_refused(self, admin):
        assert admin.post("/api/admin/accounts", json={"verb": "drop"}).status_code == 400


class TestOwnership:
    def test_it_says_whose_a_set_is(self, admin, con):
        assert (
            admin.post(
                "/api/admin/own", json={"course": "t", "set": "01", "owner": "karo"}
            ).status_code
            == 200
        )
        assert con.execute("SELECT owner FROM units WHERE id = '01'").fetchone()["owner"] == "karo"

    def test_it_refuses_an_account_that_does_not_exist(self, admin):
        answer = admin.post("/api/admin/own", json={"course": "t", "set": "01", "owner": "nobody"})
        assert answer.status_code == 404

    def test_it_refuses_a_set_that_does_not_exist(self, admin):
        answer = admin.post(
            "/api/admin/own", json={"course": "t", "set": "nie-ma", "owner": "karo"}
        )
        assert answer.status_code == 404


class TestCredentialsNeverReachThePage:
    """
    The two things an admin page must not show, both found by opening it in a
    browser rather than by a test: `users.password_hash` was listed as an
    ordinary column and was editable, and `meta.secret_key` signs session
    cookies.

    Being an admin is entitlement to everything in the database *as data*. It is
    not entitlement to become another person, and both of these are exactly that
    -- a hash you can paste into somebody's row, and a key you can forge a
    cookie with.
    """

    def test_the_password_hash_is_not_a_column_at_all(self, admin):
        body = admin.get("/api/admin/tables/users").get_json()
        assert "password_hash" not in body["table"]["columns"]
        assert all("password_hash" not in r for r in body["rows"])

    def test_no_hash_reaches_the_wire_in_any_form(self, admin):
        # Over the raw bytes, the way the answer-leak tests do it: a field
        # nobody remembered to look at is exactly the field that leaks.
        raw = admin.get("/api/admin/tables/users").get_data(as_text=True)
        assert "scrypt" not in raw

    def test_it_will_not_write_a_password_hash(self, admin, con):
        was = con.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()[0]
        admin.patch(
            "/api/admin/tables/users",
            json={"key": {"id": 1}, "values": {"password_hash": "scrypt:1:1:1$x$y"}},
        )
        now = con.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()[0]
        assert now == was, "an admin pasted a hash into somebody's row"

    def test_the_secret_key_is_listed_but_not_readable(self, admin, app):
        # The row stays visible: a key nobody can see the existence of is a key
        # somebody goes looking for in `sqlite3`.
        body = admin.get("/api/admin/tables/meta").get_json()
        rows = {r["key"]: r["value"] for r in body["rows"]}
        assert "secret_key" in rows
        assert rows["secret_key"] != app.config["SECRET_KEY"]

    def test_the_secret_key_cannot_be_changed_or_deleted(self, admin, con):
        was = con.execute("SELECT value FROM meta WHERE key = 'secret_key'").fetchone()[0]
        assert (
            admin.patch(
                "/api/admin/tables/meta",
                json={"key": {"key": "secret_key"}, "values": {"value": "mine-now"}},
            ).status_code
            == 400
        )
        assert (
            admin.delete("/api/admin/tables/meta", json={"key": {"key": "secret_key"}}).status_code
            == 400
        )
        now = con.execute("SELECT value FROM meta WHERE key = 'secret_key'").fetchone()[0]
        assert now == was

    def test_an_ordinary_meta_row_is_still_editable(self, admin, con):
        # The redaction is one row, not the table.
        assert (
            admin.patch(
                "/api/admin/tables/meta",
                json={"key": {"key": "schema_version"}, "values": {"value": "11"}},
            ).status_code
            == 200
        )


class TestTheEngineShipsNoProse:
    """
    CLAUDE.md: the UI is translated, and UI strings never appear as Python
    literals. The admin page has two places that would break it -- the banner
    saying why a table is read-only, and the refusal when somebody writes to one
    anyway -- and both send a code.
    """

    def test_no_table_carries_an_english_sentence(self, admin):
        for table in admin.get("/api/admin/tables").get_json()["tables"]:
            assert table["frozen"] in ("", "irreplaceable", "owned_elsewhere"), table["name"]
            assert table["instead"] in ("", "manage"), table["name"]

    def test_a_refusal_carries_a_code(self, admin, con):
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state) "
            "VALUES(1,'uno#recognize','sm2',1,'{}')"
        )
        con.commit()
        answer = admin.delete(
            "/api/admin/tables/card_state",
            json={"key": {"user_id": 1, "card_id": "uno#recognize"}},
        )
        assert answer.get_json()["error"] == "irreplaceable:card_state"
