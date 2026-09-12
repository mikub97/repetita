# CLAUDE.md — courses/

Course content. **Licensed CC BY-SA 4.0, not MIT** — see `LICENSE` in this
directory. This is the one place in the repository where the two licences meet,
so be careful what you move across the boundary.

## What these files are

**The published export, not the source of truth.** The database owns the material
(ADR-0006) and nothing reads this directory unless asked (ADR-0015). What lives
here is the reviewable form of a course: ordinary files, in an ordinary diff, in
an ordinary pull request — which is the whole reason the format exists and why
`repetita export` is owed rather than optional.

Editing a file here changes nothing until somebody imports it:

```bash
repetita import courses/<course>      # previews, names what it would archive, asks
repetita export <course> --to courses/<course>   # the way back out
```

Both directions are also in the app, in Manage → Import / export.

An exercise written in the Create tab exists in **no file here** until it has
been exported. That is the thing to remember when reading a course directory and
wondering where something went.

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
says the same thing; until you do, the files and the material disagree, and the
files are the half nobody is studying.

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

**A word list you wrote while studying is your own work**, and the `source:` tag
on it — `podrecznik`, `korepetycje` — says where you were when you learned the
word, not whose text it is. `bombero — strażak` is a fact about Spanish; nobody
owns it, and a list of such pairs that you typed yourself is yours to publish.

That is a different thing from the rule below it, which has not moved: **copying
a textbook's sentences, exercises or explanations is out**, at any length. The
line is between a fact you recorded and an author's expression of it. If you are
copying something somebody *wrote* rather than something a language *does*, it
needs `attribution:` and a compatible licence, or it does not go in.

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
