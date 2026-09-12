# Accounts

One copy of repetita, one database, and one row per person in it. Everybody
studies, everybody teaches, and everybody's schedule is their own.

!!! note "The login appears when the first password does"

    A database whose only account is the seeded owner, with no password, opens
    straight into the app — no login, exactly as before. Set a password on any
    account and the sign-in page appears. That is on purpose: a page asking for
    a password nobody has is a locked door with no key.

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

## Signing in, and switching

Once somebody has a password, opening the app shows a sign-in page: pick a name,
type the password.

In the app, the round chip at the top right — beside the gear — is who you are.
Click it to switch to somebody else or to sign out. Switching asks for that
person's password. That is not ceremony: these accounts live on one laptop, and
an account you can enter by picking it off a list is a label rather than an
account.

The page reloads when you switch, because everything on it — the queue, the
board, the counters — belongs to the person you are leaving.

!!! tip "Wrong password, over and over?"

    The app gives one answer for every kind of no, so "wrong password" also
    covers "no such account" and "this account is deactivated". Check with
    `repetita user list`: it shows which accounts are inactive and which have no
    password at all.

## Whose set is whose

Other people's material is **visible, not editable**. You can study from a set
somebody else wrote, read every word of it in Manage, and fix nothing in it.

```bash
repetita own en-from-pl --list                          # who owns what
repetita own en-from-pl --set radek-2 --to rzadki       # one set
repetita own en-from-pl --axis tutor --value radek --to rzadki --dry-run
```

That last form is how the English course was seeded: it had been split by a
`tutor` axis since long before accounts existed, and that axis already knew
which sets were whose.

Three ways to be allowed to change a set, and they are the whole rule:

* it belongs to nobody in particular (an empty owner — which is what everything
  imported before accounts existed says);
* it belongs to you;
* you are an admin.

A set you make in the app is yours automatically. Enforced in the store, not in
the screen: a greyed-out button is a courtesy, and the check that matters is the
one a request cannot get past.

## Which sets you study

Enrolment puts a course on your flag picker. This decides what today's queue
draws from inside it.

Every set is studied until you say otherwise — the Manage board shows a **✓** on
each one, and clicking it turns the set into a **+**: still there, still
readable, no longer in your queue. Clicking again puts it back.

```bash
repetita study en-from-pl --account karo                    # what she studies
repetita study en-from-pl --account karo --set radek-2       # add one
repetita study en-from-pl --account karo --set radek-2 --leave
repetita study en-from-pl --seed-from-owners --dry-run       # everyone -> their own
```

Three states, and they are all different:

* **never chosen** — the whole course, which is every fresh account and every
  database from before this existed. A set added tomorrow is in your queue
  without you doing anything.
* **chosen some** — those, and only those.
* **chosen none** — an empty queue. Reachable by turning every set off, and a
  legitimate thing to ask for.

!!! note "Leaving a set never costs you anything"

    Every answer and every schedule for its cards stays exactly where it is.
    A set you stop studying is hidden from the queue, not reset — rejoining is
    rejoining.

Studying somebody else's set takes no permission: you can already read every
word of it in Manage, and studying it is reading it. What ownership governs is
*changing* it.

## Enrolment

Which courses somebody has signed up for. Absence is not "cannot see it" —
material is shared and visible ([ADR-0008][adr8]) — it is "not on my flag
picker".

```bash
repetita user enrol karo en-from-pl
repetita user leave karo it-from-pl
```

In the app, the flag picker shows your courses first and the rest under **Inne
kursy**; clicking one joins it. An account enrolled in nothing sees everything,
which is every fresh install — enrolment starts mattering when you make the
first one.

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
| which courses are on your picker | |

Note which side the material is on: a set being *yours to edit* does not make it
private. Everyone can see it and study from it.

So `repetita purge --set old-stuff --with-history` deletes the material for
everybody and *your* answers only. Removing somebody else's takes `--all-users`,
which asks in the same way `--with-history` does.

One thing deliberately crosses the line: renaming a tag rewrites **every**
plan that prioritised it, including other people's. The material changed for
everybody, so a priority naming the old tag now names nothing — repairing only
your own plan would break theirs. See [ADR-0016][adr16].

## Running inside another application

Repetita can be mounted in a host application, and then the host owns the shell:
it supplies an `identity` callable saying who is signed in, and repetita never
shows a login, never sets a session cookie, and never asks for a password. A
host that supplies nothing gets the owner, which is what every deployment got
before accounts existed.

[adr16]: ../architecture/decisions/0016-accounts-and-whose-history-it-is.md

[adr8]: ../architecture/decisions/0008-the-management-surface-sees-everything.md
