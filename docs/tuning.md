# Tuning constants

Every knob in the engine gets a row here saying **which symptom it treats** and,
where one exists, **the measurement that set it**. A constant without a reason is
a constant nobody can safely change: the next person either leaves it alone
forever or moves it blindly, and both are bad.

Status column:

* **measured** — a number came from real data, and it is quoted here.
* **reasoned** — argued from a specific failure that was observed, not measured.
* **conventional** — borrowed from another system. Fair game to measure properly.

## `srs/sm2.py`

| Constant | Value | Status | Why |
| :-- | :-- | :-- | :-- |
| `LEARNING_STEPS` | 1 | reasoned | A brand-new card used to jump straight to a one-day interval: seen once, on the day you learn it, then not again until tomorrow. For intensive study that wastes the best moment there is. One learning step brings it back later in the *same* session. |
| `MAX_INTERVAL` | 90 days | reasoned | Beyond a season, an interval stops being a schedule and becomes a bet. Also the point at which a card is a candidate for retirement instead. |
| `EASE_START` | 2.5 | conventional | SM-2's original value. |
| `EASE_MIN` / `EASE_MAX` | 1.3 / 2.8 | conventional | SM-2's floor; the ceiling is ours, to stop a run of EASY answers pushing a card past the maximum interval in three reviews. |
| `LAPSE_PENALTY` | 0.20 | conventional | |
| `FUZZ` | ±5% | reasoned | Without it, a batch of cards added on one day comes back on one day, forever. |
| `HARD_MULTIPLIER` | 1.2 | conventional | Anki's value. **Not measured.** See ADR-0002 — this constant only started doing anything at all in this rewrite, so there is no history to measure it against yet. |
| `HARD_EASE_PENALTY` | 0.15 | conventional | As above. |
| `MATURE_DAYS` | 21 | conventional | Anki's threshold for "mature". |
| `LEECH_LAPSES` | 6 | reasoned | Lower than Anki's 8: at six lapses the problem is usually the *item*, not the learner, and it should be rewritten rather than drilled. |
| `RETIRE_AT_INTERVAL` | 90 | reasoned | Paired with `MAX_INTERVAL`: a card that has reached the ceiling with a clean run has nothing left to prove. |
| `RETIRE_CLEAN_REPS` | 5 | reasoned | `reps` resets to 0 on any lapse, so this *is* "no lapses in the last five reviews". |

## `srs/fsrs_backend.py`

FSRS's own 21 parameters are **not** listed here: they are the library's fitted
defaults, deliberately not copied into this repo, and the optimizer will replace
them per-learner once there is a review log worth fitting (~512 reviews). What
follows is only the wiring this app chose around them.

| Constant | Value | Status | Why |
| :-- | :-- | :-- | :-- |
| `DESIRED_RETENTION` | 0.9 | conventional | FSRS's default, and Anki's. The knob that trades workload against recall; moving it is a decision to make on real data, not a default to guess at. |
| `MAX_INTERVAL` | 90 days | reasoned | The same ceiling as `sm2.MAX_INTERVAL`, for the same reason (beyond a season an interval is a bet, not a schedule) — and because the two backends can only be compared on the author's review log if they are capped alike. FSRS's own default is 36500. |
| `LEARNING_STEPS` / `RELEARNING_STEPS` | `()` | reasoned | Empty, against FSRS's 1min/10min defaults. Day granularity is a product decision: this is a study tool, not a drill sergeant. A lapsed card returns inside the same session through the queue, not through a countdown. |
| `FUZZ` | ±5% | reasoned | Same value and same reason as `sm2.FUZZ`. FSRS's built-in fuzzing is switched off instead of used, because it reads the global `random` module and `srs/CLAUDE.md` rule 1 allows no randomness that is not injected. |

## `policies/` — not yet ported

These live in the private predecessor and come across in phase 2. Recording them
now so the reasoning is not lost in the move; the measurements are real.

| Constant | Value | Status | Why |
| :-- | :-- | :-- | :-- |
| `GATE_THRESHOLD` | 0.75 | **measured** | Lowered from 0.85 on 2026-09-01. Measured accuracy over the first three days of real use was 65% (195/302), so a 0.85 gate was shut more often than open — and what it shut out was that day's lesson, the one thing that must not wait. |
| `GATE_WINDOW` | 20 answers | reasoned | Short enough to react within a session, long enough not to swing on two answers. |
| `GATE_MIN_ANSWERS` | 8 | reasoned | Below this there is not enough evidence to judge on, so the gate stays open. It is a brake for evidence of overload, not a hurdle to clear before starting. |
| `NEW_EVERY` | 3 | **measured** | New cards are woven into the owed ones, one after every three due. Straight concatenation was the bug this replaces: with 45 cards owed and a batch capped at 40, that day's lesson did not appear in the batch at all. |
| `LESSON_FRESH_DAYS` | 3 | reasoned | How recent a lesson has to be to jump the gate. |
| `LESSON_INTRO_CAP` | 12 | reasoned | Without a cap, "exempt from the gate" just means "no gate" on a forty-word lesson day. |
| `DAILY_TARGET` | 30 | reasoned | A day counts as done at zero owed **or** this many answers. The second route exists because with a real backlog the first is unreachable, and a streak that can never move measures nothing. |

## Changing one of these

1. Say which symptom you are treating. If you cannot name one, do not change it.
2. Prefer measuring over arguing: the review log has the data, and
   `repetita compare-schedulers` (phase 3) is the harness.
3. Update the row, including the status column. A `conventional` that you
   measured becomes `measured`, and that is a real contribution.
