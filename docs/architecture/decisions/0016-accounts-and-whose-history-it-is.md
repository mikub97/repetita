# ADR-0016: Accounts, and whose history it is

**Status:** accepted, 2026-09-12
**Context:** [ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0008](0008-the-management-surface-sees-everything.md),
CLAUDE.md rule 1, `ROADMAP.md`

## Context

Four people are now using one repetita: the author and three others, who are
each other's teachers as well as each other's students. Everyone studies and
everyone may write and manage material.

The schema was built for this and never used it. Nine tables carry
`user_id INTEGER NOT NULL DEFAULT 1`, nine indexes lead with it, two make it
part of the primary key — and until this change **the string "user" did not
appear anywhere in `src/repetita/web/`.** Every store call landed on
`DEFAULT_USER = 1`, and no row existed for that number to point at.

`ROADMAP.md` listed multi-user under *"Deliberately not doing"*: *"adding the
auth layer now would be building for a user who does not exist."* That user
exists now, three times over.

## Decision

**One database, four accounts.** Not one database each: they teach each other,
so the material has to be shared, and a set of four databases with four copies
of the English course is four courses that drift.

**`user_id = 1` becomes the owner.** Every existing row stays exactly where it
is. A database with a year of history in it becomes a database with a year of
*that person's* history by gaining one row, and the seeded account is called
`owner` rather than anybody's name — whose database this is belongs to the
deployment, which is the same argument as the fourth rule about languages.

**The material is shared; the history is not.** This is the line everything
else follows from:

| one person's | everybody's |
| :-- | :-- |
| `card_state`, `review_log` | notes, cards, distractors |
| `study_plans`, `plan_priorities`, `plan_knobs` | units, tags, facets |
| `card_reports` | courses and their configuration |
| staged edits in `pending_changes` | |
| per-account UI settings in `containers` | |

**Other people's material is visible, not editable.** You can study from a set
somebody else wrote and you cannot change it. Enforced in `store/`, not in the
UI: a greyed-out button is a courtesy, and the check that matters is the one a
request cannot get past.

**Identity has one name and two sources.** `init_app(..., identity=...)` is how
a host says who is signed in; standalone, a login page and a session cookie fill
the same slot. One `g.user` either way, and `api._user_id()` is the single
function the rest of the application asks.

## Why the fixes came first, in a change of their own

Five queries in this repository would have destroyed or exposed another
person's data the moment a second account existed, and three of them were
`DELETE`s:

* `purge` deleted **every** user's `review_log`, `card_state`, `card_reports`
  and staged edits for a note — rule 1 broken by omission rather than by
  decision;
* `plans.get` / `set_priorities` / `set_knobs` / `delete` / `latest_revision`
  took a plan id alone, and a plan id is an autoincrementing integer shared by
  every account;
* `tags.rename` rewrote every plan's priorities matching on the value with no
  axis, and ran over every course in the database while accepting a `course`
  argument it used only for the alias row;
* `cards.reclassify` re-filed user 1 and left everybody else's `bucket` stale;
* the Manage board's mastery join had no `user_id`, so with two accounts it
  showed whoever had got furthest behind.

They were fixed and pinned **before** anything could create a second account —
each with a test checked by reverting the fix. Shipping them alongside the thing
that creates the account would have made the window between the two a matter of
merge order.

Found while threading identity through the request path: `daily.build_session`,
`owed_count`, `forecast`, `day_done`, `planned.build_planned_session` and
`preview` all read `all_states(con)`, which means user 1. That is the study loop
itself serving one person's due cards to whoever asked.

## Consequences

* **The login appears when the first password does.** A database whose only
  account is the seeded owner, with no password, gets none — a page asking for a
  password nobody has is a locked door with no key, and that database is every
  fresh install, every test, and the one the author's hub has been serving all
  along. No existing test had to be weakened to accommodate accounts.
* **A mounted repetita never asks for a password**, sets no `SECRET_KEY` and
  shows no login. `init_app`'s every config key is a `setdefault` for this
  reason, and a `SECRET_KEY` set there would invalidate every session the host
  had issued.
* **A host naming an account that does not exist is a loud failure**, not a
  fallback to the owner. Falling back would file somebody's answers under
  another person's name, and every one of those rows is a fact about a person's
  memory.
* **One answer for every kind of no.** Which of "no such account", "wrong
  password" and "deactivated" it was is not something a sign-in page should be
  able to tell apart.
* **Accounts are deactivated, never deleted.** `card_state` and `review_log`
  carry the id, and those rows outlive any decision about an account (rule 1).
* **One write still crosses accounts on purpose.** Renaming a tag rewrites
  every plan that prioritised it, including other people's. The material changed
  for everybody, so a priority naming the old tag now names nothing: repairing
  only the caller's plan would break the other three. It is a repair rather than
  an edit — the axis, the rank, the weight and the plan are untouched — and it
  is written down here because it is the exception.
* **Passwords are never arguments.** No `--password` flag, and a test asserts
  there never is one. Hashed with `hashlib.scrypt` in werkzeug's format, in
  `store/` and in the standard library, because a password hash is a column
  rather than a web concern.
* ADR-0008's premise has changed, and its amendment records that.

## Amendment, 2026-09-12: what the admin page will not touch

The admin page is generic over the schema, and the decision worth recording is
which tables it refuses to write to -- because that is not presentation, it is
the difference between a useful screen and the way a month of study disappears
at one in the morning.

**Three kinds of table.** *Cannot be rebuilt*: `review_log`, `card_state`
(rule 1) and `plan_revisions`, which is the row an answer points at to say what
plan it was given under. *Owned by another surface*: `notes`, `cards`, `units`,
`note_facets`, `distractors`, `card_handles` -- a write to any of them owes
`edited_at`, `content_hash`, re-expansion and `reclassify`, and
`store/material.py` exists to owe them. *Its own*: everything else.

**The plan for this change said to route content edits through
`store/material.py`. That was not done, deliberately.** A generic editor mapping
arbitrary row edits onto `stage`, `save_set` and `apply_pending` is a mapping
that has to be right for every column of every content table, and a
half-correct mapping is worse than a refusal that names where to go instead. So
content tables are read-only here and the page says which tab to use. The one
content field an admin genuinely needs -- `units.owner` -- gets a route of its
own, writing one column that owes nothing.

**Credentials are not data.** `users.password_hash` is not returned and cannot
be written; `meta.secret_key` is returned as dots and cannot be written or
deleted. Being an admin is entitlement to the database as data. It is not
entitlement to become another person, and both of those are exactly that -- a
hash to paste into somebody's row, a key to forge a cookie with. Found by
opening the page in a browser rather than by a test, which is the argument for
opening it.
