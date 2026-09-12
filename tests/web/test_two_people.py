"""
Two people, one course, one database.

The schema was built for this and never used it: every `user_id` column
defaulted to 1 and nothing ever set it to anything else. These are the tests
that the second account is real -- that each person's queue, counters, board and
plans are their own, and that neither can see the other's progress anywhere in
any response.

The last of those is asserted over the raw bytes rather than the parsed JSON,
for the same reason `test_manage_api.py` does it for answers: a field nobody
remembered to look at is exactly the field that leaks.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, date, datetime

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
    tags: [A1, liczby]
    notes:
      - id: uno
        l2: uno
        l1: jeden
      - id: due
        l2: due
        l1: dwa
      - id: tre
        l2: tre
        l1: trzy
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


def signed_in_as(app, name, password):
    client = app.test_client()
    assert client.post("/api/login", json={"name": name, "password": password}).status_code == 200
    return client


def answer(con, user_id, card_id, *, due, bucket="young"):
    con.execute(
        "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,seen,correct,bucket) "
        "VALUES(?,?,'sm2',1,'{}',?,3,3,?)",
        (user_id, card_id, due, bucket),
    )
    con.execute(
        "INSERT INTO review_log(user_id,card_id,rating,review_datetime,day,algo,mode) "
        "VALUES(?,?,3,?,?,'sm2','session')",
        (user_id, card_id, datetime.now(UTC).isoformat(), date.today().isoformat()),
    )
    con.commit()


class TestSigningIn:
    def test_the_login_appears_once_somebody_has_a_password(self, app):
        # And not before: a fresh database whose only account is the seeded
        # owner gets no login, which is what keeps every other test honest.
        assert app.test_client().get("/", follow_redirects=False).status_code == 302

    def test_the_wrong_password_is_refused(self, app):
        assert (
            app.test_client()
            .post("/api/login", json={"name": "karo", "password": "m-pass"})
            .status_code
            == 401
        )

    def test_an_unknown_account_looks_exactly_like_a_wrong_password(self, app):
        # Which of the two it was is not something a sign-in page should be able
        # to tell apart.
        unknown = app.test_client().post("/api/login", json={"name": "nobody", "password": "x"})
        wrong = app.test_client().post("/api/login", json={"name": "karo", "password": "x"})
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.get_json() == wrong.get_json()

    def test_an_api_call_while_signed_out_is_a_refusal_not_a_login_page(self, app):
        # A redirect would arrive at `fetch` as HTML parsed as JSON, which is a
        # confusing way to learn you are signed out.
        out = app.test_client().get("/api/state")
        assert out.status_code == 401
        assert out.get_json()["error"] == "not_signed_in"

    def test_signing_out_ends_it(self, app):
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/state").status_code == 401

    def test_a_deactivated_account_stops_being_signed_in(self, app, con):
        client = signed_in_as(app, "karo", "k-pass")
        U.set_active(con, "karo", False)
        assert client.get("/api/state").status_code == 401


class TestEachPersonsOwnProgress:
    @pytest.fixture
    def studied(self, app, con):
        """mikub is behind on two cards; karo on one, and a different one."""
        cards = sorted(r["id"] for r in con.execute("SELECT id FROM cards"))
        assert len(cards) >= 3, cards
        yesterday = "2020-01-01"
        answer(con, 1, cards[0], due=yesterday)
        answer(con, 1, cards[1], due=yesterday)
        answer(con, 2, cards[2], due=yesterday)
        return cards

    def test_the_owed_counter_is_your_own(self, app, studied):
        mine = signed_in_as(app, "mikub", "m-pass").get("/api/state").get_json()
        hers = signed_in_as(app, "karo", "k-pass").get("/api/state").get_json()
        assert mine["owed"] == 2
        assert hers["owed"] == 1

    def test_the_answers_today_counter_is_your_own(self, app, studied):
        mine = signed_in_as(app, "mikub", "m-pass").get("/api/state").get_json()
        hers = signed_in_as(app, "karo", "k-pass").get("/api/state").get_json()
        assert mine["answered_today"] == 2
        assert hers["answered_today"] == 1

    def test_the_session_serves_your_own_due_cards(self, app, studied):
        mine = signed_in_as(app, "mikub", "m-pass").get("/api/session").get_json()
        hers = signed_in_as(app, "karo", "k-pass").get("/api/session").get_json()
        # Handles, not card ids (ADR-0005), so compare the counts and the fact
        # that the queues are not the same queue.
        assert len(mine["cards"]) >= 1 and len(hers["cards"]) >= 1
        assert mine["cards"][0]["id"] != hers["cards"][0]["id"]

    def test_the_manage_board_shows_your_own_badges(self, app, con, studied):
        con.execute("UPDATE card_state SET bucket = 'mature' WHERE user_id = 1")
        con.commit()
        mine = signed_in_as(app, "mikub", "m-pass").get("/api/material").get_json()
        hers = signed_in_as(app, "karo", "k-pass").get("/api/material").get_json()
        assert {n["state"] for n in mine["notes"]} != {n["state"] for n in hers["notes"]}

    def test_a_plan_is_not_visible_to_anybody_else(self, app):
        mine = signed_in_as(app, "mikub", "m-pass")
        made = mine.post("/api/plans", json={"name": "Liczby", "course": "t"})
        assert made.status_code in (200, 201), made.get_json()
        plan_id = made.get_json()["id"]

        hers = signed_in_as(app, "karo", "k-pass")
        assert hers.get("/api/plans").get_json()["plans"] == []
        # Not listed, not readable, not editable, not deletable -- and a 404
        # rather than a 403, because from where she stands there is no plan 1.
        assert hers.put(f"/api/plans/{plan_id}", json={"knobs": {}}).status_code == 404
        assert hers.post(f"/api/plans/{plan_id}/preview", json={}).status_code == 404
        assert hers.delete(f"/api/plans/{plan_id}").status_code == 404
        assert mine.get("/api/plans").get_json()["plans"], "still there afterwards"

    def test_one_persons_answer_does_not_move_anybody_elses_schedule(self, app, con, studied):
        before = con.execute("SELECT due FROM card_state WHERE user_id = 2").fetchone()["due"]
        client = signed_in_as(app, "mikub", "m-pass")
        session = client.get("/api/session").get_json()
        handle = session["cards"][0]["id"]
        client.post("/api/answer", json={"id": handle, "given": "uno"})
        after = con.execute("SELECT due FROM card_state WHERE user_id = 2").fetchone()["due"]
        assert after == before


class TestNobodyElsesProgressCrossesTheWire:
    def test_no_response_carries_another_accounts_rows(self, app, con):
        cards = sorted(r["id"] for r in con.execute("SELECT id FROM cards"))
        answer(con, 2, cards[0], due="2020-01-01", bucket="mature")
        con.execute("UPDATE card_state SET retired_reason = 'karo-only-marker' WHERE user_id = 2")
        con.commit()
        client = signed_in_as(app, "mikub", "m-pass")
        for path in ("/api/state", "/api/session", "/api/material", "/api/catalogue?dim=unit"):
            body = client.get(path).get_data(as_text=True)
            assert "karo-only-marker" not in body, path


class TestEnrolment:
    """
    Which flags you see. Absence from `enrolments` is "not on my flag picker",
    never "cannot see it" -- material is shared and visible (ADR-0008), so the
    endpoint returns every course and marks each one.
    """

    def test_an_account_enrolled_in_nothing_gets_everything(self, app):
        # Every fresh install and every database that predates accounts. A
        # filtered list would be empty here, leaving a picker with nothing in it.
        body = signed_in_as(app, "karo", "k-pass").get("/api/courses").get_json()
        assert body["enrolling"] is False
        assert all(c["enrolled"] for c in body["courses"])

    def test_enrolling_marks_yours_and_leaves_the_rest_visible(self, app, con):
        from repetita.store import users as store_users

        karo = store_users.by_name(con, "karo")
        store_users.enrol(con, karo.id, "t")
        body = signed_in_as(app, "karo", "k-pass").get("/api/courses").get_json()
        assert body["enrolling"] is True
        assert {c["id"]: c["enrolled"] for c in body["courses"]} == {"t": True}

    def test_enrolments_are_one_persons(self, app, con):
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/courses/t/join").status_code == 200
        hers = client.get("/api/courses").get_json()
        his = signed_in_as(app, "mikub", "m-pass").get("/api/courses").get_json()
        assert hers["enrolling"] is True
        assert his["enrolling"] is False, "she joined; he did not"

    def test_joining_a_course_that_does_not_exist_is_refused(self, app):
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/courses/nie-ma-takiego/join").status_code == 404

    def test_leaving_keeps_the_history(self, app, con):
        client = signed_in_as(app, "karo", "k-pass")
        client.post("/api/courses/t/join")
        cards = sorted(r["id"] for r in con.execute("SELECT id FROM cards"))
        answer(con, 2, cards[0], due="2020-01-01")
        client.post("/api/courses/t/leave")
        left = con.execute("SELECT COUNT(*) AS n FROM card_state WHERE user_id = 2").fetchone()
        assert left["n"] == 1, "leaving a course is not starting again"


class TestSomebodyElsesSetIsReadOnly:
    """
    Visible, not editable. The refusal is the one a request cannot get past, so
    it is tested through the API rather than by checking a button is grey.
    """

    @pytest.fixture
    def his(self, app, con):
        from repetita.store import material

        material.set_owner(con, "t", "01", "mikub")
        return con

    def test_she_can_read_every_word_of_it(self, app, his):
        # Deliberate, and the whole of ADR-0008's amendment: these four teach
        # each other, so a Manage tab that hid his sets would hide the course.
        body = signed_in_as(app, "karo", "k-pass").get("/api/material").get_json()
        assert any(n["fields"].get("l2") == "uno" for n in body["notes"])

    def test_she_cannot_write_an_exercise_into_it(self, app, his):
        client = signed_in_as(app, "karo", "k-pass")
        before = his.execute("SELECT COUNT(*) AS n FROM notes WHERE unit = '01'").fetchone()["n"]
        answer = client.post(
            "/api/sets/01/exercises",
            json={"rows": [{"notetype": "vocab", "fields": {"l2": "tre", "l1": "trzy"}}]},
        )
        assert answer.status_code >= 400, answer.get_json()
        after = his.execute("SELECT COUNT(*) AS n FROM notes WHERE unit = '01'").fetchone()["n"]
        assert after == before, "nothing was written"

    def test_she_cannot_stage_a_change_to_it(self, app, his):
        client = signed_in_as(app, "karo", "k-pass")
        answer = client.post(
            "/api/material/stage",
            json={"note_id": "uno", "kind": "tags", "payload": ["hers-now"]},
        )
        assert answer.status_code >= 400
        row = his.execute("SELECT tags FROM notes WHERE id = 'uno'").fetchone()
        assert "hers-now" not in row["tags"]

    def test_she_cannot_remove_it(self, app, his):
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/sets/01/remove").status_code >= 400
        live = his.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE unit = '01' AND archived_at IS NULL"
        ).fetchone()["n"]
        assert live > 0

    def test_he_still_can(self, app, his):
        client = signed_in_as(app, "mikub", "m-pass")
        answer = client.post(
            "/api/material/stage",
            json={"note_id": "uno", "kind": "tags", "payload": ["his-own"]},
        )
        assert answer.status_code == 200, answer.get_json()

    def test_the_refusal_says_whose_it_is(self, app, his):
        client = signed_in_as(app, "karo", "k-pass")
        answer = client.post(
            "/api/material/stage",
            json={"note_id": "uno", "kind": "tags", "payload": ["x"]},
        )
        assert "mikub" in str(answer.get_json()), answer.get_json()


class TestWhichSetsYouStudy:
    """
    Everyone was served every set in a course, so one person's session drew from
    another's material. The queue now draws from the sets you study, and the
    default -- having chosen none -- is still all of them.
    """

    def sets(self, client):
        return {u["id"]: u["studying"] for u in client.get("/api/material").get_json()["units"]}

    def test_having_chosen_nothing_every_set_reads_as_studied(self, app):
        client = signed_in_as(app, "karo", "k-pass")
        assert all(self.sets(client).values())

    def test_leaving_one_from_that_state_leaves_exactly_one(self, app, con):
        # The case worth writing down. With no rows at all, "stop studying this
        # one" would write nothing, leave the table empty, and mean "study
        # everything" -- so the click would appear to do nothing. The others
        # have to be written down first.
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/sets/01/study", json={"studying": False}).status_code == 200
        left = self.sets(client)
        assert left["01"] is False
        assert all(v for k, v in left.items() if k != "01"), left

    def test_leaving_is_one_persons(self, app):
        mine = signed_in_as(app, "mikub", "m-pass")
        hers = signed_in_as(app, "karo", "k-pass")
        hers.post("/api/sets/01/study", json={"studying": False})
        assert self.sets(hers)["01"] is False
        assert self.sets(mine)["01"] is True, "his queue is untouched"

    def test_the_queue_empties_rather_than_refilling(self, app):
        # The course in this fixture has one set, which is exactly the case that
        # caught the first design: turning the only set off left no rows, and no
        # rows meant "study everything", so the click undid itself.
        client = signed_in_as(app, "karo", "k-pass")
        client.post("/api/sets/01/study", json={"studying": False})
        assert client.get("/api/state").get_json()["owed"] == 0
        assert client.get("/api/session").get_json()["cards"] == []

    def test_an_unknown_set_is_refused(self, app):
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/sets/nie-ma/study", json={}).status_code == 404

    def test_the_board_says_whose_each_set_is(self, app, con):
        from repetita.store import material

        material.set_owner(con, "t", "01", "mikub")
        client = signed_in_as(app, "karo", "k-pass")
        unit = next(u for u in client.get("/api/material").get_json()["units"] if u["id"] == "01")
        # She may study it and may not change it. Two questions, two answers.
        assert unit["owner"] == "mikub"
        assert unit["mine"] is False
        assert unit["studying"] is True

    def test_studying_somebody_elses_set_needs_no_permission(self, app, con):
        from repetita.store import material

        material.set_owner(con, "t", "01", "mikub")
        client = signed_in_as(app, "karo", "k-pass")
        assert client.post("/api/sets/01/study", json={"studying": True}).status_code == 200
