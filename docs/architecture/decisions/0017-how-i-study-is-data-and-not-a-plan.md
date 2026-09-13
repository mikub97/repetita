# ADR-0017: How I study is data, and it is not a plan

**Status:** accepted, 2026-09-12
**Context:** [ADR-0003](0003-scheduler-is-a-plugin.md),
[ADR-0007](0007-a-plan-is-data.md), CLAUDE.md rule 4, `docs/tuning.md`

## Context

Two things were true at once and neither was visible.

**The order new material arrived in was an accident.** `introduction_order`
sorted by lesson date, falling back to `(unit, ord, card_id)` — where `unit` is
the *directory name*. Measured on disk: `lesson:` is set in 14 of 63 note files
in `pt-br-from-pl`, and in **0** of 76, 26 and 32 in `en-from-pl`, `es-from-pl`
and `it-from-pl`. So in three courses of four the lesson branch never fired at
all, and material was introduced in alphabetical order of directory name. In
`pt-br-from-pl`, whose unit ids carry no numeric prefix, that produced:

```
fala-capoeiristas → every gram-* → licao-* → musica-* → texto-* → every vocab-*
```

Capoeira slang and the entire grammar ahead of the word for "table", and
`vocab-sala-de-aula` near the end because it begins with "v". Meanwhile
`course.yaml` declared a different and sensible order in `path:`, which has been
parsed since the first course and read by nothing: `Unit.ord` and
`Unit.requires` reached the `units` table and the `ix_units_ord` index, and no
query in `policies/` ever joined `units`.

**Half the dials were painted on.** `plans.KNOBS` declared eight tunables and
two had readers. `template_bias` was a slider on the Design tab that a learner
could drag: it wrote to `plan_knobs`, appended a `plan_revision`, and changed
nothing about any session. ADR-0007 had already written the rule it broke — *"a
knob nothing reads is a dial that does nothing, which is worse than no dial"* —
which is how a rule with no test decays.

The two are one problem: things declared in the model, stored in the database,
and read by nothing.

## Decision

**The order is the learner's to choose, and the choice is data.** A new concept,
`study_styles`, one row per `(person, course)`: which ordering introduces new
material, which orders the debt, and the knobs.

**It is not a plan, and it is not a flag on one.** ADR-0007 says a plan is an
*additional* path through the material, asked for per request, and the first
version that let an active plan take over the Study tab was reverted. That
decision stands and is not being revisited. A style is the opposite shape: it
configures the one path everybody already has, it is never asked for, and it
cannot narrow what a session draws from. Merging the two concepts because they
share a vocabulary of knobs is how the reverted version happened.

Concretely, the line that survives verbatim: **a session is built under a plan
only when the request names one.** `?plan=` still wins over any saved style.

**Absence is the default, not a missing row.** `styles.get` answers with
`styles.DEFAULT` when there is no row, and `Recipe()` with every default is
byte-for-byte the behaviour that predates this change. That is asserted, not
argued: `test_no_style_is_exactly_the_default_session` builds the same session
both ways and compares the lists.

**Every change writes a revision, and every answer records which one.** ADR-0003
applied a third time. `review_log` gains `style_revision_id` — a second column
beside `plan_revision_id`, never both set. Overloading the existing column would
be exactly the failure ADR-0007 names (*"filing it under the active one would
make every later comparison wrong"*) arriving by a different door.

The write side ships before anything reads it, as `review_log` itself did and as
`plan_revision_id` did. The reason is not symmetry: a column added in six months
leaves every answer before it unattributable, and the Study tab is where nearly
every answer in this database is given.

**Five orderings, and the default is the old behaviour under an honest name.**
`lesson` (freshest lesson, then the path), `course`, `axis`, `plan`, `shuffle`.
`shuffle` is a seeded `blake2b` per card rather than `random.shuffle`: the order
must be identical across the several fetches one day makes, adding a card must
not move the others, and no global RNG may be touched.

**`axis` is not hardcoded to `level`.** The style carries which axis, and only
axes a course declares `ordered` are offered. `level` is the one every course
declares today, and putting it in the engine would be a second
Portuguese-shaped assumption — CLAUDE.md rule 4.

## What is deliberately not implemented

**`requires`.** Every `path:` entry in all four courses has `requires: []` —
97 of 97. Enforcing prerequisites would be a mechanism with no content to act on
and no way to observe it working. It stays parsed, stored and unread until a
course actually declares one.

**`desired_retention` is retired without a replacement.** It is a property of
the scheduler, not of a session: two answers to one card under two retention
targets, with `card_state` holding a single blob, is a schedule with two authors
and no record of which wrote what. It belongs to a course- or account-level
scheduler setting, which `srs/CLAUDE.md` calls a migration.

**`template_bias` could not be honoured and became `template_order`.** A 0–3
float weighting "produce over recognise" has nothing to multiply — the
note→card model has no such scalar. What the engine *was* deciding, alphabetically
and by accident, is which sibling card of a note is introduced first. A ranked
list of template names is that decision made deliberately.

**`form_bias` became `ladder_steps`.** Which form a card is asked in is chosen by
the presenter at serialisation, not in `policies/`. The ladder's depth is the
lever that exists, and it is what the slider's own sentence already described.

## Consequences

A learner can make their queue worse, and will be able to see that they have:
the screen shows what tomorrow would hold and what the debt does over fourteen
days, because the policies are pure and a preview therefore costs nothing. That
argument is ADR-0007's, applied on the screen where it matters more.

`owed_count` takes no style and has no parameter through which one could reach
it. The debt is what the schedule says, and a preference may reorder it and
never decide it — `order_debt` returns a permutation of its input, and a test
says so.

Retired knob rows stay in `plan_knobs`, refused on write and dropped on read.
Deleting them would change what an existing `plan_revision` snapshot meant, and
those are append-only.

Three things that were parsed and unread now decide something: `course.path`,
`facet_axes.ordered`, and `daily.forecast()`. That is the rule this ADR would
like to leave behind — **if it is declared, stored and unread, either read it or
delete it.**
