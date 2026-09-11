# CLAUDE.md — Repetita

An open-source engine for learning a language from scratch. Extracted from a
private app (`~/Documents/Research/hub`, tab `pt`) that is **still in daily use**
by its author while this rewrite happens.

**Project language is English.** Code, comments, docs, commit messages, issues
and PRs — all English. The UI is translated (`pl`, `en`, `pt`) through i18n
catalogues; UI strings never appear as literals in Python.

## Read this before changing anything

* [ARCHITECTURE.md](ARCHITECTURE.md) — the note→card→form model and why it exists.
* [docs/architecture/decisions/](docs/architecture/decisions/) — ADRs. If you are
  about to propose "wouldn't it be better to…", check here first; it has probably
  been decided, with the measurement that decided it.
* Per-directory `CLAUDE.md` files in `src/repetita/srs/`, `src/repetita/content/` and
  `courses/` carry the rules specific to those areas. Read the one for the
  directory you are touching.
* [docs/inbox.md](docs/inbox.md) — the queue of raw material waiting to be turned
  into exercises, and the loop an agent runs to do it (`repetita inbox`).
* [docs/labels.md](docs/labels.md) — every exercise has a short name. Read this
  before "fixing" the fact that names repeat: that is deliberate.
* [docs/using/creating.md](docs/using/creating.md) — exercises are written in the
  app as well as in files (ADR-0010). Anything under `store/` that creates
  material must leave `origin` empty and set `edited_at`, or the next import
  archives it.

## Four rules

Short list, and it is the whole of it. Each protects something that either cannot
be undone or is the reason this project exists. Everything that used to read
*never* is now a command — see **Doing the frightening things** below.

1. **Study history is never destroyed silently.** `review_log` and `card_state`
   are the only things here that cannot be rebuilt: material comes back from
   `courses/`, a schedule does not. They can be *moved* (`repetita rename-id`)
   and, when somebody says so, deleted (`repetita purge --with-history`) — but
   only by an operation that names itself, takes a snapshot first, and reports
   what went. Nothing may quietly recompute them, and no import corrects them
   (ADR-0004).
2. **Answers must not reach the client while a question is open.** `public_card()`
   is the single serialisation path *for an open question*. If you add a field,
   decide explicitly whether it is visible before or after answering, and put it
   in the right tuple. The Manage tab is the one deliberate exception and sees
   everything, because you cannot fix a typo in an answer you cannot see — see
   ADR-0008 for why that is not the same leak.
3. **The scheduler is pure.** No clock, no database, no uninjected randomness in
   `src/repetita/srs/`. It is the one place where a subtle bug costs months of study
   before anyone notices.
4. **The engine contains no Portuguese and no Polish.** Language-specific
   behaviour is course configuration, not code. CI greps for this.

Licensing is not on this list because it is not a rule about care — it is the
condition for the repository existing. Course content is CC BY-SA 4.0, sourced
material needs `attribution:`, images need `license:`, and a mistake there cannot
be removed from git history. See [courses/CLAUDE.md](courses/CLAUDE.md).

## Doing the frightening things

Take a snapshot, then do it. `repetita snapshot "why"` is instant and consistent
against a running app, and `repetita restore <name>` puts it back. That is the
net which makes everything below reasonable rather than reckless.

| what you want | how |
| --- | --- |
| fix a wrong id | `repetita rename-id <old> <new>` — moves the history across nine tables and records the rename in `courses/<course>/renames.yaml`, which is what `check-ids` reads. **Never by hand**: editing the key in YAML detaches the history silently, which is what the old prohibition was really about. |
| get rid of material | `repetita purge <id>` / `--set <unit>` / `--archived-before <date>`. It reports what goes before it goes. Archiving is still the default, and still right for material that has simply left a course. |
| change the database directly | Allowed. `store/material.py` exists because every write owes four things — set `edited_at`, leave `content_hash` alone, re-expand cards, `reclassify` — and raw SQL owes them too. |
| restart the app, reload a course | Just do it. `scripts/restart-host.sh` snapshots first. |

### What still asks first

Three things, and they are about consequence rather than permission:

* **Deleting study history** — `purge --with-history`. Those rows cannot be
  rebuilt from anything.
* **Deleting material that is still in a course**, as opposed to archiving it.
* **Issues labelled `human-only`** — design decisions. Analyse and propose; the
  decision wants a conversation and an ADR, not a PR.

Pushing, publishing and restarting are not on that list.

## Commits

**Commit to `main` for ordinary work.** Branch protection no longer enforces
against admins, and CI runs on every push, so a break is visible within a minute.
Open a pull request when the change earns one: something worth reading as a unit,
something you want a second opinion on, or when asked.

## Layout

```
src/repetita/
  core/        notes, cards, protocols, forms, Judgement, Response -- no I/O, no Flask
  srs/         scheduler backends: sm2, fsrs6, leitner       -- pure functions
  graders/     typed, sentence, choice, self                 -- pure functions
  presenters/  which form to show a card in right now
  policies/    what goes into a session: daily, cram, test, match, rehearse
  difficulty/  which *level* to ask at (separate from *when* -- see ADR-0004)
  content/     pydantic models, YAML loader, validator, build, labels, ids
  store/       SQLite: schema, migrations, queries, the material inbox
  modes/       one module per exercise form (mirrors static/modes/*.js)
  web/         Flask blueprint, API, templates, static
courses/       course content -- CC BY-SA 4.0, NOT MIT
tests/         mirrors src/repetita/
docs/          mkdocs; ADRs under architecture/decisions/
```

## Working here

```bash
uv sync --all-extras          # or: pip install -e ".[dev]"
pytest                        # fast; no network, no real study DB
ruff check . && ruff format --check . && mypy src/repetita
repetita validate courses/pt-br-from-pl --strict
```

Tests must never touch a real study database. `tests/conftest.py` points every
`*_DB` environment variable at a throwaway file **before** any `repetita` import,
because module-level path constants are read at import time.

## Commits and PRs

* [Conventional Commits](https://www.conventionalcommits.org/) — `CHANGELOG.md`
  is generated from them, so the prefix is load-bearing, not decoration.
* Sign off (`git commit -s`): this project uses the DCO, not a CLA.
* **Never add `Co-Authored-By` trailers, tool attribution, or "generated with"
  footers to commits or PR descriptions.** Commits are authored by the person who
  owns the change. An agent's work is attributed the same way any contributor's
  is: by the commit author and the PR, not by a trailer.
* The PR template has a **"Why this way"** section. Fill it in properly. It is
  the part the maintainer reads to learn, and the part that makes a review
  possible without re-deriving your reasoning.

## Pitfalls that have already cost time here

**A failed edit looks exactly like a successful one.** `ruff format` runs on this
repository, so a block you are about to patch may not look the way it did when
you last read it -- indentation, line breaks and trailing commas all move. A
string-replacement edit that finds no match usually reports nothing and exits
zero. This has happened three times in this repository, and each time the symptom
appeared much later: a test failing for a reason that made no sense, because the
code under test was never actually changed.

When editing programmatically, assert that the edit applied:

```python
assert old in s, "target block not found -- it was probably reformatted"
p.write_text(s.replace(old, new))
```

and prefer re-reading the file over trusting what you wrote five minutes ago. If
a test fails in a way that seems impossible, check that your edit landed before
you debug the logic.

**Dataclasses here use `slots=True`.** `obj.__dict__` does not exist. Use
`dataclasses.replace(obj, field=value)`.

**Content and progress are different kinds of data.** Any change under `store/`
should answer the question "what happens to someone's existing schedule?" before
it is written, not after. Since ADR-0006 the content tables are owned rather than
rebuilt, so a mistake in them is no longer erased by the next startup — which
makes that question sharper, not softer. `card_state` is never rebuilt, ever.

## Fix what you find

Notice a second problem while working? **Fix it**, and say so in the commit
message. The old rule sent it to an issue instead, on the grounds that a change
doing two things cannot be reverted for one of them — true, and it was costing
more than it bought: three one-line fixes were filed as issues in a single
afternoon, and filing them was the last anything happened to them.

Keep the reverting argument for changes that genuinely deserve to be separable: a
migration, a scheduler change, anything under CODEOWNERS. Everywhere else, a
repository where small things get fixed beats one where they are catalogued.

Issues labelled `agent-ready` name the files, the behaviour and the test. Start
there; you are not confined to it.
