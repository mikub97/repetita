# Accounts

One copy of repetita, one database, and one row per person in it. Everybody
studies, everybody teaches, and everybody's schedule is their own.

!!! note "Signing in comes next"

    This page describes the accounts themselves and the command that manages
    them. There is no login screen yet: the app still opens as the owner of the
    database. That is deliberate — the accounts had to exist, and the queries
    behind them had to stop crossing between people, *before* anything could
    create a second account. The login lands in the change after this one.

## Every database already has an owner

The schema has carried a `user_id` on nine tables since it was written, always
defaulting to `1`, with nothing for that number to point at. It points at
something now: an account called `owner`, created with the database and with no
password.

If you have been studying for months, nothing about your history moves. It
becomes the owner's history by a row appearing, not by any row changing.

The first thing to do is say who you are:

```bash
repetita user rename owner --to mikub
repetita user passwd mikub
```

`passwd` asks twice and never takes the password as an argument — see below.

## Adding the others

```bash
repetita user add karo --display "Karolina"
repetita user add rzadki --display "Radek"
repetita user add weissmar --display "Małgosia"
```

Each one asks for a password. An account created with an empty password is not
an open door: nobody can sign in to it until `repetita user passwd` has run.

```bash
repetita user list
```

```
  1  mikub     en-from-pl, pt-br-from-pl  admin
  2  karo      en-from-pl
  3  rzadki    en-from-pl
  4  weissmar  en-from-pl, es-from-pl
```

## Enrolment

Which courses somebody has signed up for. Absence is not "cannot see it" —
material is shared and visible ([ADR-0008][adr8]) — it is "not on my flag
picker".

```bash
repetita user enrol karo en-from-pl
repetita user leave karo it-from-pl
```

Leaving a course removes the enrolment and nothing else. The history stays, so
rejoining is rejoining rather than starting again.

## Leaving, rather than being deleted

```bash
repetita user deactivate rzadki
```

Deactivated, never deleted. `card_state` and `review_log` carry the account's
id, and those rows outlive any decision about an account — the first of the
[four rules](../index.md). A deactivated account cannot sign in and keeps
everything it learned.

## Passwords are never arguments

There is no `--password` flag, and there is a test asserting there never is. An
argument lands in the shell history, in `ps` output, and in whatever records the
command somebody pasted into a chat window.

A pipe is allowed, because a setup script is a real thing and reading one line
from stdin leaves no trace:

```bash
printf 'a-real-password\n' | repetita user add karo
```

Stored with scrypt, salted per account, and never written anywhere as itself —
not in the database, not in a log.

## What is one person's, and what is everybody's

The line this runs along, because it is the answer to most questions about what
a command will do:

| one person's | everybody's |
| :-- | :-- |
| `card_state` — where you are with a card | the exercises themselves |
| `review_log` — every answer you have given | the sets they are in |
| `study_plans` and their priorities | tags, and what they mean |
| `card_reports` — what you flagged | the courses |
| staged edits you have not confirmed | |

So `repetita purge --set old-stuff --with-history` deletes the material for
everybody and *your* answers only. Removing somebody else's takes `--all-users`,
which asks in the same way `--with-history` does.

[adr8]: ../architecture/decisions/0008-the-management-surface-sees-everything.md
