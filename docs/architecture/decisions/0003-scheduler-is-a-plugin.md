# ADR-0003: Scheduler state is an opaque blob beside a denormalised `due`

**Status:** accepted, 2026-09-06

## Context

The project started on SM-2, whose state is five scalars (`ease`, `interval`,
`reps`, `lapses`, `due`). Those were columns in the progress table, and every
query — the session queue, the counters, the forecast — read them directly.

FSRS keeps different state: stability, difficulty, and a parameter vector. Under
the old shape, adding it meant rewriting every one of those queries. Any third
scheduler would mean doing it again.

There is also a harder constraint. Choosing between schedulers, tuning FSRS's 21
parameters, or evaluating a scheduler at all requires the **full sequence** of
reviews with the interval each was answered at. The original schema kept
aggregates (`seen`, `correct`, `wrong`) and threw the sequence away. That is the
one decision in the old design that cannot be undone after the fact.

## Decision

1. `SchedulerBackend` is a Protocol (`src/repetita/core/protocols.py`). Backends are
   pure: no clock, no database, no uninjected randomness.
2. Scheduler state is a **JSON blob in one column**, owned entirely by the
   backend. Nothing outside it may read a key.
3. `due`, `interval`, `seen`, `correct`, `wrong` and `lapses` are **denormalised
   alongside** it. The queue and the counters read only those, so they are
   independent of which backend wrote them.
4. `review_log` is append-only, in FSRS-compatible shape — `rating` 1–4,
   `review_datetime` as aware UTC, `review_duration_ms`, `elapsed_days`,
   `state_before`. Written from the first day, before anything needs it.
5. Three backends ship: `sm2`, `fsrs6`, and `leitner`. The third exists to keep
   the protocol honest — a protocol with one implementation is a description of
   that implementation. If a proposed protocol change cannot be satisfied by
   fifteen lines of Leitner, it has grown a dependency on FSRS's memory model.

## Evidence

Anki does exactly this: `cards.data` is a JSON column carrying FSRS's stability
and difficulty, sitting beside the typed `ivl`/`factor`/`due` columns. It is why
Anki could adopt FSRS without a schema migration.

On which scheduler to build: the `srs-benchmark` project evaluates schedulers
over ~350M reviews from 9,999 Anki collections. FSRS-6 scores 0.3460 log loss on
21 parameters; a 2.7M-parameter RWKV network scores 0.2773. Duolingo's published
HLR scores 0.4694 — **worse than predicting a constant** (0.3945). That rules out
HLR, and puts FSRS at the right point on the accuracy/complexity curve for an app
meant to be hacked on.

## Consequences

* `retrievability()` may return `None`. SM-2 has no memory model and must say so
  rather than return a number that looks like a probability and is not one.
  Callers that need a real probability degrade visibly.
* Choosing FSRS over SM-2 for the author's own study is an evidence-based
  decision made on the author's own review log, not on the benchmark. Both run
  in parallel first.
* FSRS's optimizer pulls `torch` (~2GB) and is therefore an optional extra that
  nothing in the serving path imports. It also does nothing below ~512 reviews.
* FSRS requires timezone-aware UTC datetimes. Day boundaries are local. The
  conversion happens at exactly one boundary and is tested — this codebase has
  already shipped that bug once.
