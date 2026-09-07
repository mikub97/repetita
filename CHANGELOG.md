# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/). Generated from Conventional Commits.

## [Unreleased]

### Added
- **Content model.** pydantic models are the source of truth; the loader reports
  every problem with its file and note id, and refuses rather than coerces --
  an answer of `no`, which YAML 1.1 reads as `False`, is reported with the fix
  instead of becoming a note whose correct answer is the string "False".
- **note → card expansion.** One authored note becomes as many cards as its note
  type has satisfiable templates, each with its own schedule. A template's
  `requires` means the card is not generated when the field is absent, so a note
  with no audio simply has no listening card and gains one the day audio exists.
- **`store`.** SQLite schema, content sync, and an append-only `review_log` in
  FSRS shape from the first answer, including `state_before`. Content is a cache
  rebuilt on every load; `card_state` never is.
- **`policies.daily`.** Due work, gated introductions woven into it, and
  consolidation only once both are exhausted. Counters are one number per idea.
- **`web`.** `/api/state`, `/api/session`, `/api/answer` and one page built from
  native ES modules with no bundler. Serialisation has exactly one path.
- **`repetita import-hub`.** Brings a predecessor database's material and history
  across, idempotently, and emits a course directory. Verified on a real corpus:
  676 notes, 676 cards, 99 card states, 454 reviews.
- **`repetita validate` and `repetita check-ids`.** The second is the one that
  protects learners: an item id is a scheduling key, so a rename silently deletes
  progress, and CI now refuses it.
- **`fsrs6` scheduler backend.** Registered, not switched to: `DEFAULT` stays
  `sm2`, because which scheduler to use is an evidence decision on a real review
  log rather than a default change.
- **`presenters.ladder`.** A card never answered is asked in a recognition form;
  from the second encounter it is asked as declared. Keyed on `seen`, not `reps`,
  which resets on every lapse.

- Project scaffolding: `src/` layout, MIT licence for code and CC BY-SA 4.0 for
  course content, CI (lint, types, tests on 3.11–3.13), issue and PR templates,
  CODEOWNERS.
- `core`: `Rating`, `Response`, `Judgement`, and the `Grader`,
  `SchedulerBackend` and `Presenter` protocols. `Response` carries an audio
  channel from the start so speaking practice does not require rewriting every
  grader later.
- `graders`: `typed`, `sentence`, `choice`, `self`. Accent folding, punctuation
  handling and sentence slack are course configuration rather than engine
  behaviour.
- `srs`: `sm2` and `leitner` backends behind one protocol. Leitner exists to keep
  the protocol honest.
- Property tests (`hypothesis`) covering interval bounds, due-date monotonicity
  and purity of `review()`.

### Fixed
- **The client is never sent a card id.** Ids are authored from the material, so
  `obrigado#produce` carried its own answer -- 167 of 676 cards on the first real
  corpus. A field filter cannot help, because an id is not a field. The client
  gets an opaque per-run handle and the id stays on the server.
  ([ADR-0005](docs/architecture/decisions/0005-the-client-never-sees-a-card-id.md))
- **A note's siblings are held back from today rather than requeued.** Moving
  them to the end of the queue read as burying and was not: the whole queue ships
  in one batch, so both still reached the learner in one sitting -- the thing
  ADR-0001 requires this to prevent. ([#27](../../issues/27))
- **The importer emits course files.** It wrote notes into the content cache,
  which the next startup rebuilds from disk, so serving anything wiped the
  imported material. ([#22](../../issues/22))
- **The lesson introduction cap is spent on lesson material only.** Every card
  met for the first time today was charged to it, so back-catalogue
  introductions silently shrank the day's lesson allowance -- and on a day with a
  real backlog closed the lesson exemption entirely, the one thing it must never
  do. Counted with `store.lesson_first_seen_on` now. ([#19](../../issues/19))
- **Cards owed on the same day come out in a defined order.** `scheduled_cards`
  had no `ORDER BY`, so ties -- most of a real backlog -- were arranged by the
  query planner and could change under a SQLite upgrade, an added index or an
  `ANALYZE`. They are ordered by content now (`unit`, `ord`, card id), the same
  tie-break `introduction_order` uses. ([#20](../../issues/20))
- **HARD no longer resets a card's schedule.** In the app this was extracted
  from, HARD fell below the pass threshold and was handled identically to AGAIN:
  a missing accent erased a 45-day interval and recorded a lapse, which is
  precisely what the grading code's own docstrings said must not happen. No test
  covered it. See [ADR-0002](docs/architecture/decisions/0002-hard-is-a-pass.md).
