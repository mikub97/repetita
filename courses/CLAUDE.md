# CLAUDE.md — courses/

Course content. **Licensed CC BY-SA 4.0, not MIT** — see `LICENSE` in this
directory. This is the one place in the repository where the two licences meet,
so be careful what you move across the boundary.

## The thing to be careful about

**Never rename an item `id` by editing a file.**

It is a scheduling key. A key changed in YAML silently deletes every learner's
progress on that item, and nothing in the interface reveals that it happened —
the card simply reappears as new, months of history gone. CI checks this against
`main` on every pull request.

But an id that is wrong can be put right, which it could not before:

```bash
repetita rename-id <old> <new>
```

It moves the exercise **and its history** across nine tables in one transaction,
and writes the rename into `renames.yaml` beside this file, which is what
`check-ids` reads — a rename and a disappearance look identical from CI's side,
and the record is what tells them apart. Export afterwards so the course file
says the same thing.

Fixing a typo in an exercise is free and encouraged, and is still a different
operation from renaming its id.

Material can also be deleted outright now — `repetita purge` — though archiving
remains the default, and is still the right answer for material that has simply
left a course.

## Provenance

Every item must be original work or come from a source compatible with
CC BY-SA 4.0. Where it derives from a third-party source, the `attribution:`
field is mandatory — Tatoeba sentences (CC BY 2.0 FR) and Wikimedia images being
the common cases. `docs/THIRD-PARTY.md` lists what may and may not be used.

**Images require a `license:` field and a link to the source. No exceptions.**
This is not bureaucracy: the predecessor of this project is a repository that can
never be made public, because 54 copyrighted illustrations are in its git
history, and deleting the files in a later commit does not remove them from
history. The cost of getting this wrong once is the whole repository,
permanently.

What cannot be accepted, at any length: textbook sentences, material from
commercial courses (Duolingo, Babbel, Memrise), song lyrics still in copyright.

## Writing an exercise

The engine knows nothing about the language being taught. Everything
language-specific — whether accents are folded, how much slack a sentence gets,
how tracks are weighted — is declared in `course.yaml`. If you find yourself
wanting to change engine code to make an exercise work, that is the signal that
a course setting is missing; say so in the issue rather than reaching into
`src/`.

Before opening a PR:

```bash
repetita validate courses/<course-id> --strict
```

An answer must never be visible before the learner answers. See
`src/repetita/content/CLAUDE.md` for which fields are shown when.
