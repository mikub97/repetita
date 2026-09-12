"""
`repetita user`, and the one thing it must never do.

A password given as an argument lands in the shell history, in `ps` output, and
in whatever records the command somebody pasted into a chat window. There is
deliberately no `--password` flag, and the test that matters here is the one
asserting there never is.
"""

from __future__ import annotations

import io

import pytest

from repetita import store
from repetita.cli import main
from repetita.store import users as U


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    store.connect(path).close()
    return path


@pytest.fixture(autouse=True)
def piped(monkeypatch):
    """Stand in for a pipe, which is how a setup script supplies a password."""

    def feed(text: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    return feed


def con(db):
    return store.connect(db)


class TestNoPasswordOnTheCommandLine:
    def test_there_is_no_password_flag(self, db, capsys):
        # The refusal that matters. If this ever starts passing, somebody has
        # added the convenience and with it a password in `~/.zsh_history`.
        with pytest.raises(SystemExit):
            main(["user", "add", "karo", "--password", "hunter2", "--db", str(db)])
        assert "unrecognized arguments" in capsys.readouterr().err

    def test_a_piped_password_works_and_signs_in(self, db, piped):
        piped("hunter2\n")
        assert main(["user", "add", "karo", "--db", str(db)]) == 0
        c = con(db)
        assert U.authenticate(c, "karo", "hunter2") is not None
        c.close()


class TestTheCommands:
    def test_list_shows_the_seeded_owner(self, db, capsys, piped):
        piped("")
        assert main(["user", "list", "--db", str(db)]) == 0
        out = capsys.readouterr().out
        assert U.OWNER in out
        assert "no password yet" in out, "an account nobody can sign in to should say so"

    def test_renaming_the_owner_is_how_a_deployment_says_whose_it_is(self, db, capsys, piped):
        piped("")
        assert main(["user", "rename", U.OWNER, "--to", "mikub", "--db", str(db)]) == 0
        c = con(db)
        assert U.by_id(c, U.DEFAULT_USER).name == "mikub"
        c.close()

    def test_enrol_and_leave(self, db, capsys, piped):
        piped("x\n")
        main(["user", "add", "karo", "--db", str(db)])
        assert main(["user", "enrol", "karo", "en-from-pl", "--db", str(db)]) == 0
        c = con(db)
        assert U.enrolments(c, "karo") == ["en-from-pl"]
        c.close()
        assert main(["user", "leave", "karo", "en-from-pl", "--db", str(db)]) == 0
        c = con(db)
        assert U.enrolments(c, "karo") == []
        c.close()

    def test_deactivating_is_not_deleting(self, db, piped):
        piped("x\n")
        main(["user", "add", "karo", "--db", str(db)])
        assert main(["user", "deactivate", "karo", "--db", str(db)]) == 0
        c = con(db)
        karo = U.by_name(c, "karo")
        assert karo is not None and not karo.active
        c.close()

    def test_an_unknown_account_is_a_refusal_not_a_traceback(self, db, capsys, piped):
        piped("")
        assert main(["user", "passwd", "nobody", "--db", str(db)]) == 1
        assert "no account" in capsys.readouterr().out
