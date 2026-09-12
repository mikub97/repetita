"""
Who is signed in.

Repetita has spent its whole life as one person's application, and the schema
has spent its whole life ready for it not to be: nine tables carry a `user_id`
that nothing ever set. This module is the half of that which faces a request.

**Two sources, one answer.** A host that mounts repetita supplies an `identity`
callable and repetita never asks anybody for a password -- the host owns the
shell, which is what `app.init_app` has said since it was written. Standalone,
repetita owns the shell, and this module is that shell: a login page, a session
cookie, and a switcher. Either way the rest of the application reads `_user()`
and cannot tell which happened.

**The login appears when the first password does.** A database whose only
account is the seeded owner, with no password, gets no login -- a page asking
for a password nobody has is a locked door with no key, and that database is
every fresh install and every test. `repetita user passwd` is what turns it on,
so it is a decision somebody took rather than a surprise they walked into.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..store import db as store_db
from ..store import users as store_users
from ..store.users import User

#: Where the signed-in account id is kept. `flask.session` is a cookie signed
#: with the app's `SECRET_KEY` -- the client can read it and cannot change it,
#: which is all this needs: the id is not a secret, it is a claim.
SESSION_KEY = "repetita_user"

auth = Blueprint("repetita_auth", __name__, template_folder="templates")


def db() -> sqlite3.Connection:
    """
    The connection for this request, opened once and closed at teardown.

    Lives here rather than in `api` so that this module and that one share a
    connection without importing each other.
    """
    con: sqlite3.Connection | None = g.get("repetita_db")
    if con is None:
        con = store_db.connect(current_app.config["REPETITA_DB"])
        g.repetita_db = con
    return con


def close_db(_: BaseException | None = None) -> None:
    con: sqlite3.Connection | None = g.pop("repetita_db", None)
    if con is not None:
        con.close()


def login_offered() -> bool:
    """
    Does this deployment ask for a password?

    Both halves matter. `REPETITA_LOGIN` is False when repetita is mounted in
    somebody else's application, because authentication is theirs. And even
    standalone, an account nobody can sign in to is not worth a page.
    """
    return bool(current_app.config.get("REPETITA_LOGIN")) and store_users.anybody_can_sign_in(db())


def signed_in() -> User | None:
    """The account named by the session cookie, if it still exists and is live."""
    claimed = session.get(SESSION_KEY)
    if claimed is None:
        return None
    found = store_users.by_id(db(), int(claimed))
    if found is None or not found.active:
        # Deactivated, renamed away, or a cookie from another database. Not an
        # error -- it reads as signed out, which is what it is.
        session.pop(SESSION_KEY, None)
        return None
    return found


def current_user() -> User:
    """
    Who this request is for, resolved once and remembered on `g`.

    The order is the whole design: a host's answer wins, then the session, then
    the owner. The last of those is what every unscoped call in this repository
    has meant since the schema was written, so a deployment that adopts none of
    this behaves exactly as it did.
    """
    found: User | None = g.get("repetita_user")
    if found is None:
        found = _resolve()
        g.repetita_user = found
    return found


def _resolve() -> User:
    con = db()
    identity = current_app.config.get("REPETITA_IDENTITY")
    if identity is not None:
        named = identity()
        if named is not None:
            # Loudly, not quietly. A host that says "karo" and gets the owner
            # would file Karo's answers under somebody else's name, and every
            # one of those rows is a fact about a person's memory.
            return store_users.resolve(con, named)
    if current_app.config.get("REPETITA_LOGIN"):
        by_session = signed_in()
        if by_session is not None:
            return by_session
    owner = store_users.by_id(con, store_users.DEFAULT_USER)
    if owner is None:  # pragma: no cover -- seeded with the schema
        raise LookupError("this database has no accounts at all")
    return owner


#: Paths that must work while signed out, or there is no way to sign in.
OPEN = ("login", "sign_in", "static")


@auth.before_request
def guard() -> Any:
    """
    Refuse a request that has nobody behind it.

    Registered on repetita's own blueprints only -- here and, at the bottom of
    `api`, on the one the rest of the application lives on -- so a host's routes
    are never touched by it. On the blueprint rather than in `init_app` because
    a blueprint is registered once per process and mounted possibly more than
    once; hanging it off the mount raises on the second call.

    Does nothing at all where `login_offered()` is False, which is every mounted
    deployment and every database without a password.
    """
    if not login_offered() or signed_in() is not None:
        return None
    if (request.endpoint or "").rsplit(".", 1)[-1] in OPEN:
        return None
    if "/api/" in request.path:
        # An API call gets a refusal it can act on. A redirect would arrive at
        # `fetch` as a login page parsed as JSON, which is a confusing way to
        # learn you are signed out.
        return jsonify({"error": "not_signed_in"}), 401
    return redirect(url_for("repetita_auth.login"))


@auth.get("/login")
def login() -> Any:
    """The sign-in page. Redirects away when there is nothing to sign in to."""
    if not login_offered() or signed_in() is not None:
        return redirect(url_for("repetita.index"))
    return render_template(
        "login.html",
        accounts=[
            {"name": u.name, "display": u.label}
            for u in store_users.everyone(db())
            if u.has_password
        ],
    )


@auth.post("/api/login")
def sign_in() -> tuple[Response, int] | Response:
    """
    Check a password, and start a session.

    One answer for every kind of no. Which of "no such account", "wrong
    password" and "deactivated" it was is not something a sign-in page should be
    able to tell apart, and `store.users.authenticate` is where that is decided.
    """
    body = request.get_json(silent=True) or {}
    who = store_users.authenticate(db(), str(body.get("name", "")), str(body.get("password", "")))
    if who is None:
        return jsonify({"error": "wrong_password"}), 401
    session.clear()
    session[SESSION_KEY] = who.id
    session.permanent = True
    return jsonify({"user": as_json(who)})


@auth.post("/api/logout")
def sign_out() -> Response:
    session.clear()
    return jsonify({"ok": True})


@auth.get("/api/me")
def me() -> Response:
    """
    Who I am, and who I could become.

    The switcher needs the list, and the list is not a secret: these four people
    teach each other and can already see each other's material (ADR-0008). What
    it does not carry is anything about anybody's progress.
    """
    who = current_user()
    con = db()
    return jsonify(
        {
            "user": as_json(who),
            "login": login_offered(),
            "accounts": [
                {"name": u.name, "display": u.label}
                for u in store_users.everyone(con)
                if u.has_password and u.id != who.id
            ],
        }
    )


def as_json(who: User) -> dict[str, Any]:
    """What the client is told about an account. Never the hash, never an email."""
    return {"name": who.name, "display": who.label, "admin": who.is_admin}
