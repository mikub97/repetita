# CLAUDE.md — Roda

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
* Per-directory `CLAUDE.md` files in `src/roda/srs/`, `src/roda/content/` and
  `courses/` carry the rules specific to those areas. Read the one for the
  directory you are touching.

## Rules that are not negotiable

1. **Never change an existing item `id` in `courses/`.** Scheduling state is keyed
   on it. A renamed id silently deletes a learner's progress on that item and
   nothing in the UI reveals it. CI checks this against `main`; do not work around
   the check. Adding and removing are fine — renaming is not.
2. **`progress` / `card_state` is never rebuilt from content.** Content is a cache
   and is wiped and re-derived on every load. Study history is not, ever.
3. **Answers must not reach the client while a question is open.** `public_item()`
   is the single serialisation path. If you add a field, decide explicitly whether
   it is visible before or after answering, and put it in the right tuple.
4. **The engine contains no Portuguese and no Polish.** Language-specific
   behaviour is course configuration, not code. CI greps for this.
5. **The scheduler is pure.** No clock, no database, no uninjected randomness in
   `src/roda/srs/`. It is the one place where a subtle bug costs months of study
   before anyone notices.
6. **No direct pushes to `main`.** Branch, PR, green CI. This applies to agents
   especially.

## Layout

```
src/roda/
  core/        notes, cards, protocols, Judgement, Response  -- no I/O, no Flask
  srs/         scheduler backends: sm2, fsrs6, leitner       -- pure functions
  graders/     typed, sentence, choice, self                 -- pure functions
  presenters/  which form to show a card in right now
  policies/    what goes into a session: daily, cram, test, match, rehearse
  difficulty/  which *level* to ask at (separate from *when* -- see ADR-0004)
  content/     pydantic models, YAML loader, validator, build
  store/       SQLite: schema, migrations, queries
  modes/       one module per exercise form (mirrors static/modes/*.js)
  web/         Flask blueprint, API, templates, static
courses/       course content -- CC BY-SA 4.0, NOT MIT
tests/         mirrors src/roda/
docs/          mkdocs; ADRs under architecture/decisions/
```

## Working here

```bash
uv sync --all-extras          # or: pip install -e ".[dev]"
pytest                        # fast; no network, no real study DB
ruff check . && ruff format --check . && mypy src/roda
roda validate courses/pt-br-from-pl --strict
```

Tests must never touch a real study database. `tests/conftest.py` points every
`*_DB` environment variable at a throwaway file **before** any `roda` import,
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

## Scope discipline

Issues labelled `agent-ready` are specified down to the files to change, the
expected behaviour, and the test that must pass. Do that, and nothing else.
If you find a second bug on the way, open an issue for it rather than fixing it
in the same PR — a PR that does two things cannot be reverted for one of them.

Issues labelled `human-only` are design decisions, not tasks. Do not implement
them; comment with analysis if you have any.
