# Repetita

An open, hackable engine for learning a language **from scratch** — and a first
course, Brazilian Portuguese for Polish speakers.

Repetita sits between a flashcard app and a language course. It takes the part of
Anki that works (spaced repetition over independently scheduled cards), the part
of Quizlet that works (many ways to study the same material), and the part of
Duolingo that works (an ordered path you can actually start from zero), and
keeps all three open, inspectable and modifiable.

> **Status: early, and it runs.** A full study session works end to end — load a
> course, get today's queue, answer, get scheduled. It was built by extracting
> the engine from a private app in daily use, and that app's material and
> history import cleanly. Nothing is stable yet: expect the content format and
> the API to move.

## What makes it different

**One note becomes many cards.** You write a word or a sentence once. The course
declares which *directions* it should be tested in — recognition, production,
listening, spelling — and each becomes a separate card with its own schedule.
You may recognise *saudade* and be unable to produce it; those are two facts,
and Repetita tracks them as two.

**The scheduler is a plugin.** SM-2 and FSRS-6 ship in the box behind one
protocol, and a 15-line Leitner backend exists purely to prove the protocol
isn't secretly FSRS-shaped. Bring your own.

**Exercises that give away their own answer are refused, not warned about.** A
hint reading `fim de semana = weekend` for the answer `fim de semana` is a bug
that silently teaches nothing, and it is invisible in review. Repetita quarantines
such items in CI, so they can never reach a learner.

**Content is data, and it is meant to be forked.** Courses are directories of
YAML validated against a published JSON Schema. Adding a lesson is a pull
request a non-programmer can make.

## The name

*Repetita iuvant* — "things repeated help." Someone stated the premise of spaced
repetition about two thousand years before anyone measured it.

## Licence

Code is **MIT** ([LICENSE](LICENSE)). Course content is **CC BY-SA 4.0**
([courses/LICENSE](courses/LICENSE)). Third-party sources and their terms are
listed in [docs/THIRD-PARTY.md](docs/THIRD-PARTY.md).

## Acknowledgements

Repetita is not the first to work these problems out, and it borrows openly:

* **[FSRS](https://github.com/open-spaced-repetition/py-fsrs)** (MIT) — the
  scheduler, and the [srs-benchmark](https://github.com/open-spaced-repetition/srs-benchmark)
  that settled which algorithm to build on.
* **[Anki](https://apps.ankiweb.net/)** (AGPL) — the note→card model. Read for
  its ideas, not copied.
* **[LibreLingo](https://github.com/LibreLingo/LibreLingo)** (AGPL; courses
  CC BY-SA) — course-as-directory, licence as course metadata, and the insight
  that exercises should be *generated* from content rather than authored.
* **[quenti](https://github.com/quenti-io/quenti)** (AGPL) — per-scope session
  state, and precomputed distractors as content rather than a runtime query.
