"""
Accounts, and what each one is signed up for.

Nine tables in this schema have carried `user_id INTEGER NOT NULL DEFAULT 1`
since they were written, nine indexes lead with it, and two make it part of the
primary key. Nothing ever set it to anything but 1. This module is the row that
number finally points at, and the reason the rest of `store/` can stop assuming.

**Every database has an owner.** `db._apply_schema` seeds `id = 1` when the
table is empty, so a database with a year of history in it becomes a database
with a year of *somebody's* history by gaining one row -- no existing row moves,
and no query has to cope with a `user_id` pointing at nothing. That account is
named `owner` until somebody renames it, because a name is deployment and this
package is not.

**Passwords are hashed here, in the standard library.** `hashlib.scrypt`, in
exactly the format `werkzeug.security` writes, so the two can read each other's
hashes and neither is required. Flask is not imported by `store/`, and a
password hash is a column rather than a web concern -- it has no more business
knowing about a request than `card_state` does.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

#: The account every `user_id` column defaults to. Seven modules used to declare
#: this separately, which is seven chances for them to disagree about who is
#: studying; they now import it from here.
DEFAULT_USER = 1

#: What the seeded owner is called before anybody renames it. Deliberately not a
#: person's name: whose database this is belongs to the deployment, not to the
#: engine (see the fourth rule in CLAUDE.md, which is the same argument about
#: languages).
OWNER = "owner"

#: scrypt cost, `werkzeug.security`'s defaults. Written into every hash, so
#: raising them later leaves old hashes readable.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
#: 128 * N * R is what scrypt needs; this is that with room to spare.
MAXMEM = 132 * 1024 * 1024


class UnknownUser(LookupError):
    """No such account. Carries the name asked for, so the caller can say it."""


class NameTaken(ValueError):
    """`users.name` is unique: it is what somebody types to sign in."""


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str
    display: str = ""
    is_admin: bool = False
    active: bool = True
    created_at: str = ""
    #: Whether a password has been set -- not the hash, which never leaves this
    #: module. "Nobody can sign in as this account yet" is a thing the CLI and
    #: the admin page need to show; the hash itself is not.
    has_password: bool = False

    @property
    def label(self) -> str:
        """What to show. Falls back to the name, which is never empty."""
        return self.display or self.name


def hash_password(password: str, *, salt: str | None = None) -> str:
    """
    A password in the form it is allowed to be stored in: never as itself.

    The `salt` argument exists for tests that need a hash to be reproducible.
    Nothing else should pass it.
    """
    salt = salt or secrets.token_hex(8)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=MAXMEM,
    )
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}${salt}${derived.hex()}"


def verify(stored: str, password: str) -> bool:
    """
    Does `password` produce `stored`?

    An empty hash is an account with no password set, and that is *not* an
    account anybody can sign in to -- it is one waiting for `repetita user
    passwd`. Returning True there would make the seeded owner an open door.
    """
    if not stored or not password:
        return False
    try:
        method, salt, want = stored.split("$", 2)
        kind, n, r, p = method.split(":")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    try:
        got = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=MAXMEM,
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(got.hex(), want)


def _row(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=int(row["id"]),
        name=row["name"],
        display=row["display"] or "",
        is_admin=bool(row["is_admin"]),
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
        has_password=bool(row["password_hash"]),
    )


def by_id(con: sqlite3.Connection, user_id: int) -> User | None:
    return _row(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def by_name(con: sqlite3.Connection, name: str) -> User | None:
    return _row(
        con.execute("SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()
    )


def everyone(con: sqlite3.Connection, *, include_inactive: bool = False) -> list[User]:
    """Every account, oldest first -- which puts the owner first."""
    where = "" if include_inactive else " WHERE active = 1"
    rows = con.execute(f"SELECT * FROM users{where} ORDER BY id")
    return [u for u in (_row(r) for r in rows) if u is not None]


def resolve(con: sqlite3.Connection, who: str | int | None) -> User:
    """
    A name, an id, or nothing at all -- and always an account or an exception.

    `None` means the owner, which is what every unscoped call in this repository
    has meant since the schema was written. The CLI leans on that: `--user` is
    optional everywhere and omitting it keeps today's behaviour exactly.
    """
    if who is None:
        found = by_id(con, DEFAULT_USER)
        if found is None:  # pragma: no cover -- seeded by _apply_schema
            raise UnknownUser(f"this database has no account {DEFAULT_USER}")
        return found
    if isinstance(who, int) or (isinstance(who, str) and who.isdigit()):
        found = by_id(con, int(who))
    else:
        found = by_name(con, str(who))
    if found is None:
        known = ", ".join(u.name for u in everyone(con, include_inactive=True)) or "none"
        raise UnknownUser(f"no account {who!r}; this database has: {known}")
    return found


def add(
    con: sqlite3.Connection,
    name: str,
    *,
    password: str = "",
    display: str = "",
    is_admin: bool = False,
) -> User:
    """Create an account. An empty password is one nobody can sign in to yet."""
    name = name.strip()
    if not name:
        raise NameTaken("an account needs a name")
    if by_name(con, name) is not None:
        raise NameTaken(f"there is already an account called {name!r}")
    with con:
        cur = con.execute(
            "INSERT INTO users (name, display, password_hash, is_admin, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (
                name,
                display,
                hash_password(password) if password else "",
                1 if is_admin else 0,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    created = by_id(con, int(cur.lastrowid or 0))
    assert created is not None
    return created


def set_password(con: sqlite3.Connection, who: str | int, password: str) -> User:
    user = resolve(con, who)
    with con:
        con.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password) if password else "", user.id),
        )
    return user


def authenticate(con: sqlite3.Connection, name: str, password: str) -> User | None:
    """
    The one place a password is checked. `None` covers every kind of no, on
    purpose: which of "no such account", "wrong password" and "deactivated" it
    was is not something a sign-in page should be able to tell apart.
    """
    row = con.execute(
        "SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name.strip(),)
    ).fetchone()
    if row is None:
        # Hash anyway. Returning immediately makes an unknown name measurably
        # faster than a wrong password, which is how a list of accounts leaks.
        verify(hash_password("no such account"), password)
        return None
    user = _row(row)
    if user is None or not user.active:
        return None
    return user if verify(row["password_hash"] or "", password) else None


def set_active(con: sqlite3.Connection, who: str | int, active: bool) -> User:
    """
    Deactivate rather than delete. `card_state` and `review_log` carry this id,
    and those rows outlive any decision about an account (rule 1).
    """
    user = resolve(con, who)
    with con:
        con.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user.id))
    found = by_id(con, user.id)
    assert found is not None
    return found


def rename(con: sqlite3.Connection, who: str | int, new_name: str) -> User:
    """
    Change what somebody signs in as, carrying their sets with them.

    `units.owner` holds a name rather than an id, so a rename that only touched
    this table would quietly orphan every set that person owns -- the same shape
    of mistake as editing an exercise id by hand (ADR-0011).
    """
    user = resolve(con, who)
    new_name = new_name.strip()
    if not new_name:
        raise NameTaken("an account needs a name")
    clash = by_name(con, new_name)
    if clash is not None and clash.id != user.id:
        raise NameTaken(f"there is already an account called {new_name!r}")
    with con:
        con.execute("UPDATE users SET name = ? WHERE id = ?", (new_name, user.id))
        con.execute("UPDATE units SET owner = ? WHERE owner = ?", (new_name, user.name))
    found = by_id(con, user.id)
    assert found is not None
    return found


def enrol(con: sqlite3.Connection, who: str | int, course: str) -> User:
    """Sign somebody up for a course. Idempotent: joining twice is joining."""
    user = resolve(con, who)
    with con:
        con.execute(
            "INSERT INTO enrolments (user_id, course, joined_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, course) DO NOTHING",
            (user.id, course, datetime.now(UTC).isoformat(timespec="seconds")),
        )
    return user


def unenrol(con: sqlite3.Connection, who: str | int, course: str) -> User:
    """
    Leave a course. Removes the enrolment and nothing else -- the history stays,
    so rejoining is rejoining rather than starting again.
    """
    user = resolve(con, who)
    with con:
        con.execute("DELETE FROM enrolments WHERE user_id = ? AND course = ?", (user.id, course))
    return user


def enrolments(con: sqlite3.Connection, who: str | int | None = None) -> list[str]:
    """Which courses somebody is signed up for, in the order they joined."""
    user = resolve(con, who)
    rows = con.execute(
        "SELECT course FROM enrolments WHERE user_id = ? ORDER BY joined_at, course", (user.id,)
    )
    return [r["course"] for r in rows]
