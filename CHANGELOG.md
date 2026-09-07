# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/). Generated from Conventional Commits.

## [Unreleased]

### Added
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
