# ADR-0015: YAML is only a way in and a way out

**Status:** accepted, 2026-09-12
**Context:** completes [ADR-0006](0006-the-database-owns-the-material.md) and
[ADR-0010](0010-material-can-be-written-in-the-app.md)

## Context

ADR-0006 decided that the database owns the material and `courses/*.yaml` is an
import/export format. ADR-0010 finished the writing half: an exercise can begin
life in the app. Both were accepted, and `ARCHITECTURE.md` has said so for weeks.

One thing never followed. `build_library` opened with `load_course(course_dir)`
and `sync()`, and took the course, its units, its facets and its exercise types
from those files. So:

* the app could not start without a course directory on disk, however full the
  database was;
* **every startup performed a merge** -- the one operation that archives material
  in bulk, overwrites content and raises conflicts -- against whatever files
  happened to be there. On a working tree that is a branch, a half-finished edit,
  or a rebase;
* there was no `repetita import`, because import was not something anyone asked
  for;
* `POST /api/reload` re-read the files, so the only route to refreshing the
  running app went through YAML;
* export existed and was reachable only from a command line, and never wrote
  `notetypes.yaml`, so a course that declared its own exercise type did not
  survive the round trip.

This is the same shape of gap ADR-0006's own amendment records: the decision was
made, and one call site went on doing the old thing. The cost is not theoretical.
Restarting the app is the most ordinary act there is, `scripts/restart-host.sh`
does it on a timer's whim, and each time it ran a merge nobody had asked for.

## Decision

**Nothing reads `courses/` unless somebody asks it to.**

`build_library(db_path, course_id)` opens no file. Course, units, facets,
exercise types and material all come from the connection.

Material arrives two ways, and both say what they did:

* `repetita import <dir|zip>`, and
* the Import / export panel in Manage, which carries a course as a zip in both
  directions.

An import previews first, **names every exercise it would archive one by one**,
and takes a snapshot before it writes.

### Scope is derived from the source

A source with a `course.yaml` at its root is a whole course, and archives
material it does not contain -- that is how deleting an exercise from a file
reaches the course. A source without one is a fragment: it says nothing about
material it does not mention, and archives nothing.

Derived, never asked for. A flag would put the distinction in the hands of
whoever remembers to pass it, on the one operation where forgetting is expensive
and the mistake is a course archived in full.

### The one exception: seeding

A course the database has never heard of is imported from its directory on first
start, and said so in the log. This is less an exception than the absence of
anything for the rule to protect: there is no material to conflict with, nothing
to archive, no edit to lose.

Keyed on *that course*, not on the database being empty -- "empty" would mean the
second course anybody added never seeded at all.

### `courses/` is the published export

It stays in git, keeps its CC BY-SA licence, and keeps `validate` and
`check-ids` in CI. What changes is what it *is*: the reviewable form of the
material, refreshed by `repetita export`, not the thing the app reads.

The loop is explicit in both directions and neither end runs by itself.

## Consequences

* **A note the loader refuses is now stored**, expands into no cards, and is
  refused again by `web/app._servable` on the way out. It used to be dropped on
  the floor, which was survivable while a file was the source of truth and is not
  once the database is: a note that exists nowhere is one nobody can find, fix,
  or even know about. The quarantine is enforced at the serving boundary, which
  is where it has to be anyway -- an exercise edited in the app passes no loader.
* **Course-declared exercise types live in a table** (schema 9). They were parsed
  and thrown away, and a database-only start would have quarantined every note
  using one.
* `repetita validate --db` checks the material as stored, which is what a learner
  actually gets. The file mode is unchanged and is what CI runs. Both call
  `validate.check`, so `content/CLAUDE.md` rule 5 -- one implementation of every
  rule -- holds on the check itself rather than on the loader.
* `repetita serve` takes a course id or a directory.
* `POST /api/reload` rebuilds from the database. Import, then reload: two steps,
  each saying what it did. It was one step that hid the other, and an import is
  not a thing to perform by accident while refreshing a screen.
* **An import only ever touches its own course.** `sync` read every note in the
  database regardless of course, so importing a second course archived the whole
  of the first. Invisible while a database held one course, and total the first
  time one held two -- which seeding a second course makes ordinary.
* `_content_hash` treats tags as a set. They are a set everywhere else, and
  hashing them in order meant an export and a straight re-import reported every
  note as changed and every locally edited one as a conflict.
* Study history is untouched by all of it. `card_state` and `review_log` are not
  read, written or considered by any of this, which is the property
  `TestHistoryDoesNotMove` and `TestOwnership` exist to keep true.

## What was rejected

**Re-import when the files are newer than `courses.imported_at`.** Keeps a degree
of file-following and would have been the smallest change. It is the same bug in
a smaller window: the merge still runs when nobody asked, and now on a condition
nobody can see. The question an import answers -- *is this file or this row the
one you meant?* -- has an owner, and a timestamp comparison is not it.

**Refusing to seed at all, so nothing reads YAML ever.** The strictest reading,
and it buys nothing: an empty database has no truth to protect, and the cost is
an extra step on every fresh clone, paid forever, to rule out a hazard that does
not exist.

**A CI check that `courses/` matches what the database would export**, catching an
edit made in the app and never exported. Wanted, and it needs a reference
database CI does not have. Left undone deliberately rather than approximated.

**Accepting a bare `notes.yaml` upload in the browser.** A fragment needs a course
named for it and a rule about what it may not do, and neither is expressible in a
file picker. `repetita import` is where that lives.
