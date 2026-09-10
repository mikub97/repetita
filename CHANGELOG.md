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
- **"I know this".** One button, taking a card out of the queue on the learner's
  word. Recorded as `declared` and never as `earned`, so a claim stays
  distinguishable from months of evidence, with an undo at the only moment the
  learner knows which card they meant. It writes nothing to the review log:
  declaring is not an answer, and letting it in would corrupt every accuracy
  figure computed from it -- including the gate that decides how fast new
  material arrives.
- **`presenters.ladder`.** A card never answered is asked in a recognition form;
  from the second encounter it is asked as declared. Keyed on `seen`, not `reps`,
  which resets on every lapse.
- **Reporting a broken exercise.** One understated control beside the question
  and on the verdict screen, where a wrong answer key is actually discovered. It
  files a report with a reason code, suspends the card so a bug stops costing
  reviews, and offers an undo in the moment. Kept out of the review log, because
  a report is not an answer; kept out of the content cache, because that is wiped
  and re-derived from the very files the report is complaining about -- so it
  snapshots the note as authored, and `repetita reports` prints it later beside
  the file and line to go and fix.

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
- **The `choice` form is rendered.** It was added to `SUPPORTED_FORMS` and served
  with shuffled options, and no client module was ever written for it, so the
  client hit `MODES[card.form] === undefined` and **silently skipped the card** --
  546 of 676 at first contact. `tests/web/test_forms.py` now reads both the
  server's list and the client's registry, because the gap was between them and
  no single-sided test could see it.
- **Cards retire.** `should_retire()` existed, had a unit test, and nothing
  called it; no card would ever have left the queue. The rule moved to
  `core/retirement.py`, because it is policy over what the store records rather
  than a property of a memory model -- FSRS holds that nothing is ever finished
  and Leitner has no notion of a clean run.
- **An answer given offline is kept.** It used to be written to a status line and
  lost. Handles are persisted as a consequence: a queued answer posted after a
  restart would otherwise resolve to nothing.
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
