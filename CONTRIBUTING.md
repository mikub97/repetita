# Contributing

Two kinds of contribution, with different rules: **code** (MIT) and **course
content** (CC BY-SA 4.0). Both are welcome; the content path is deliberately open
to people who do not write Python.

## Sign-off

This project uses the [DCO](https://developercertificate.org/), not a CLA.
Commit with `git commit -s`; that appends a `Signed-off-by:` line asserting you
have the right to submit the work under the project's licence.

**Do not add `Co-Authored-By` trailers, tool attribution, or "generated with"
footers.** A commit is authored by the person who owns the change.

## Contributing content

1. Find or open a [content issue](../../issues/new?template=content.yml).
2. Edit or add a file under `courses/<course-id>/units/<unit>/notes/`.
3. `roda validate courses/<course-id> --strict`
4. Open a PR. CI will check the schema, the answer-leak rules, distractor
   availability, and id stability, and will comment with how many cards your
   change adds.

### Rules that PRs are rejected for

* **Never rename or delete an existing item `id`.** It is a scheduling key.
  Renaming it silently deletes every learner's progress on that item and nothing
  in the UI reveals it. Fix the text, keep the id.
* **A field visible before answering must not contain the answer.** `prompt`,
  `cue`, `hint`, `situation`, `translation` and `instruction` are shown before;
  `answers`, `explain`, `target` and `source` after. An item whose cue reads
  `fim de semana = weekend` for the answer `fim de semana` teaches nothing, and
  the problem is invisible in review. CI quarantines these.
* **Material must be yours, or compatible with CC BY-SA 4.0.** Textbook
  sentences, content from commercial courses (Duolingo, Babbel, Memrise), and
  in-copyright song lyrics cannot be accepted, however small the excerpt.
  Sources that work are listed in [docs/THIRD-PARTY.md](docs/THIRD-PARTY.md);
  where an item derives from one, record it in the item's `attribution:` field.
* **Images need a `license:` and a link to the source.** No exceptions. A
  repository that accumulates unlicensed images can never be made public — that
  is not hypothetical, it is why this project is a new repository rather than a
  rename of its predecessor.

## Contributing code

```bash
uv sync --all-extras          # or: pip install -e ".[dev]"
pre-commit install
pytest
ruff check . && ruff format --check . && mypy src/roda
```

Read [ARCHITECTURE.md](ARCHITECTURE.md) and the
[ADRs](docs/architecture/decisions/) first. The layering table in ARCHITECTURE.md
is enforced by review: `core/` imports nothing from the rest of the package, and
`srs/` cannot reach a database or a clock.

### Adding an extension

A new scheduler, grader, presenter or session policy is a new file plus a
registry entry. It should not require changing anything else. If it does, say so
in the PR — that is a design problem worth discussing, not a detail to work
around.

New schedulers must pass `tests/srs/test_protocol.py` unmodified. That file
drives every backend through the protocol alone and never reads a key out of a
state dict; if your backend needs it to, the protocol is missing a method.

### Commits

[Conventional Commits](https://www.conventionalcommits.org/) — the changelog is
generated from them.

```
feat(srs): add FSRS-6 backend
fix(content): reject choice items with fewer than three distractors
docs(adr): record why HARD is a pass
```

Keep a PR to one thing. A PR that does two cannot be reverted for one of them.

## For agents

See [CLAUDE.md](CLAUDE.md) and the per-directory `CLAUDE.md` files. Issues
labelled `agent-ready` name the files to change, the expected behaviour, the test
that must pass, and what is off-limits. Do that and nothing else; if you find a
second problem, open an issue rather than folding it into the same PR.

Issues labelled `human-only` are design decisions, not tasks.
