"""
Accounts.

Nine tables have carried `user_id INTEGER NOT NULL DEFAULT 1` since they were
written and nothing ever set it to anything else. These are the tests for the
row that number points at -- and for the two things that make an account an
account rather than a label: a password that is never stored as itself, and the
fact that deactivating one does not take its history with it.
"""

from __future__ import annotations

import pytest

from repetita import store
from repetita.store import users as U


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


class TestTheOwner:
    def test_every_database_has_one(self, con):
        # `DEFAULT_USER` is what every unscoped call in this repository means.
        # A database where it points at nothing is a database where every one
        # of those calls is a lie.
        owner = U.by_id(con, U.DEFAULT_USER)
        assert owner is not None
        assert owner.name == U.OWNER
        assert owner.is_admin, "it is their database"

    def test_an_existing_database_gains_one_without_moving_a_row(self, con, tmp_path):
        # The migration case: a year of history recorded before accounts
        # existed becomes a year of the owner's history, and nothing is
        # rewritten to make that true.
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state) "
            "VALUES(1,'casa#recognize','sm2',1,'{}')"
        )
        con.commit()
        con.close()
        again = store.connect(tmp_path / "t.db")
        row = again.execute("SELECT user_id FROM card_state").fetchone()
        assert row["user_id"] == U.DEFAULT_USER
        assert U.by_id(again, U.DEFAULT_USER) is not None
        again.close()

    def test_it_is_seeded_once_and_not_again(self, con, tmp_path):
        U.rename(con, U.DEFAULT_USER, "mikub")
        con.close()
        again = store.connect(tmp_path / "t.db")
        assert [u.name for u in U.everyone(again)] == ["mikub"], "seeded over a renamed owner"
        again.close()


class TestPasswords:
    def test_a_password_is_never_stored_as_itself(self, con):
        U.add(con, "karo", password="a-real-password")
        row = con.execute("SELECT password_hash FROM users WHERE name = 'karo'").fetchone()
        assert "a-real-password" not in row["password_hash"]
        assert row["password_hash"].startswith("scrypt:")

    def test_the_right_password_signs_in(self, con):
        U.add(con, "karo", password="hunter2")
        assert U.authenticate(con, "karo", "hunter2") is not None

    def test_the_wrong_one_does_not(self, con):
        U.add(con, "karo", password="hunter2")
        assert U.authenticate(con, "karo", "hunter3") is None

    def test_an_account_with_no_password_is_not_an_open_door(self, con):
        # The seeded owner starts this way, and so does anything `user add`
        # creates with an empty prompt. It is an account waiting for
        # `user passwd`, not one anybody can walk into.
        U.add(con, "karo")
        assert U.authenticate(con, "karo", "") is None
        assert U.authenticate(con, "karo", "anything") is None

    def test_a_deactivated_account_cannot_sign_in(self, con):
        U.add(con, "karo", password="hunter2")
        U.set_active(con, "karo", False)
        assert U.authenticate(con, "karo", "hunter2") is None

    def test_changing_a_password_invalidates_the_old_one(self, con):
        U.add(con, "karo", password="hunter2")
        U.set_password(con, "karo", "hunter3")
        assert U.authenticate(con, "karo", "hunter2") is None
        assert U.authenticate(con, "karo", "hunter3") is not None

    def test_two_accounts_with_the_same_password_do_not_look_alike(self, con):
        # Salted. Without that, the hash column tells anybody who reads it which
        # accounts share a password.
        U.add(con, "karo", password="same")
        U.add(con, "rzadki", password="same")
        hashes = {r["password_hash"] for r in con.execute("SELECT password_hash FROM users")}
        assert len(hashes) == 3, "one per account, plus the owner's empty one"

    def test_a_hash_from_another_scheme_is_refused_rather_than_crashed_on(self, con):
        assert not U.verify("md5$abc$def", "whatever")
        assert not U.verify("nonsense", "whatever")


class TestAccounts:
    def test_a_name_is_taken_only_once(self, con):
        U.add(con, "karo")
        with pytest.raises(U.NameTaken):
            U.add(con, "Karo")

    def test_an_unknown_account_says_which_ones_there_are(self, con):
        U.add(con, "karo")
        with pytest.raises(U.UnknownUser, match="karo"):
            U.resolve(con, "nobody")

    def test_deactivating_keeps_the_history(self, con):
        # Rule 1. `card_state` and `review_log` carry this id, and those rows
        # outlive any decision about an account.
        karo = U.add(con, "karo")
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state) "
            "VALUES(?,'casa#recognize','sm2',1,'{}')",
            (karo.id,),
        )
        con.commit()
        U.set_active(con, "karo", False)
        assert con.execute("SELECT COUNT(*) AS n FROM card_state").fetchone()["n"] == 1
        assert U.by_name(con, "karo") is not None

    def test_a_rename_carries_the_sets_that_person_owns(self, con):
        # `units.owner` holds a name, not an id. A rename that touched only the
        # `users` row would orphan every set they own -- the same shape of
        # mistake as editing an exercise id by hand (ADR-0011).
        U.add(con, "karo")
        con.execute(
            "INSERT INTO units(course, id, owner) VALUES('t', '01', 'karo')",
        )
        con.commit()
        U.rename(con, "karo", "karolina")
        owner = con.execute("SELECT owner FROM units WHERE id = '01'").fetchone()["owner"]
        assert owner == "karolina"

    def test_a_rename_onto_a_taken_name_is_refused(self, con):
        U.add(con, "karo")
        U.add(con, "rzadki")
        with pytest.raises(U.NameTaken):
            U.rename(con, "karo", "rzadki")

    def test_the_hash_never_leaves_the_store(self, con):
        # `User` carries whether a password is set, because the CLI and the
        # admin page have to show that. It does not carry the hash.
        karo = U.add(con, "karo", password="hunter2")
        assert karo.has_password
        assert "hash" not in str(karo)
        assert not U.add(con, "rzadki").has_password


class TestEnrolment:
    def test_joining_twice_is_joining(self, con):
        U.add(con, "karo")
        U.enrol(con, "karo", "en-from-pl")
        U.enrol(con, "karo", "en-from-pl")
        assert U.enrolments(con, "karo") == ["en-from-pl"]

    def test_leaving_keeps_the_history(self, con):
        # Absence from `enrolments` is "not on my flag picker", not "start
        # again": rejoining has to be rejoining.
        karo = U.add(con, "karo")
        U.enrol(con, "karo", "en-from-pl")
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state) "
            "VALUES(?,'casa#recognize','sm2',1,'{}')",
            (karo.id,),
        )
        con.commit()
        U.unenrol(con, "karo", "en-from-pl")
        assert U.enrolments(con, "karo") == []
        assert con.execute("SELECT COUNT(*) AS n FROM card_state").fetchone()["n"] == 1

    def test_enrolments_are_one_persons(self, con):
        U.add(con, "karo")
        U.add(con, "rzadki")
        U.enrol(con, "karo", "en-from-pl")
        U.enrol(con, "rzadki", "it-from-pl")
        assert U.enrolments(con, "karo") == ["en-from-pl"]
        assert U.enrolments(con, "rzadki") == ["it-from-pl"]
