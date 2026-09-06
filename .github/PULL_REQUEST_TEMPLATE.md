## What changed

<!-- One or two sentences. What does this PR do? -->

## Why this way

<!-- REQUIRED. Not what you changed -- why this approach and not an obvious
     alternative. If you rejected another design, say which and why.
     This is the section the maintainer reads to learn. Do not skip it. -->

## How it was verified

<!-- Commands you ran, and what you saw. "CI is green" is not sufficient on its
     own for anything touching srs/, store/ or content loading. -->

## Checklist

- [ ] Commits are signed off (`git commit -s`) — this project uses the DCO
- [ ] No `Co-Authored-By` or tool-attribution trailers
- [ ] Conventional Commit prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- [ ] Tests added or updated; `pytest`, `ruff`, `mypy` pass locally
- [ ] **No existing item `id` in `courses/` was renamed or removed** (see CLAUDE.md rule 1)
- [ ] This PR does one thing

### If this PR touches `courses/`

- [ ] I wrote this content myself, or it comes from a source compatible with
      CC BY-SA 4.0, and I have recorded the source in the item's `attribution:` field
- [ ] It is **not** copied from a textbook, a commercial course (Duolingo, Babbel,
      Memrise…), or song lyrics still in copyright
- [ ] Any image carries a `license:` and a link to its source
- [ ] `roda validate` passes with `--strict`
