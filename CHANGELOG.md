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
- **HARD no longer resets a card's schedule.** In the app this was extracted
  from, HARD fell below the pass threshold and was handled identically to AGAIN:
  a missing accent erased a 45-day interval and recorded a lapse, which is
  precisely what the grading code's own docstrings said must not happen. No test
  covered it. See [ADR-0002](docs/architecture/decisions/0002-hard-is-a-pass.md).
