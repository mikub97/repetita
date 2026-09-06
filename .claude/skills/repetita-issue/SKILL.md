---
name: repetita-issue
description: Turns a rough idea, bug report or TODO into a properly specified GitHub issue for this repo — "open an issue for this", "zrób z tego issue", "this should be a task". Writes it in the definition-of-ready format that agents can act on, picks the right labels, and files it with gh.
---

# Rough thought → an issue an agent can actually do

A badly specified issue costs more than the work it describes: whoever picks it
up spends their budget rediscovering context that was already in someone's head.
This skill's whole job is to move that context into the issue.

## The test for whether an issue is ready

An issue is `agent-ready` when someone who has never seen this repository could
do it without asking a question. Concretely, all four of these are present:

1. **Goal** — what is true afterwards, as behaviour, not implementation.
2. **Files to change** — named. Plus what is off-limits. This is the single
   biggest lever on cost: it is the difference between reading three files and
   reading thirty.
3. **Test that must pass** — the acceptance criterion, as a test name or a
   command with its expected output.
4. **Context an agent cannot infer** — prior decisions, the reason the obvious
   approach is wrong, a link to the relevant ADR.

If you cannot fill in (2) or (3), the issue is not ready. Label it `needs-spec`
and say what is missing. Do not guess at file paths to make the form look
complete — a wrong path is worse than an absent one.

## Steps

**1. Decide whether it is a task at all.**
Design decisions are not tasks. "Should the scheduler be swappable per course or
per user?" is `human-only`: it wants a conversation and an ADR, not a PR. Label
it that way and write the trade-off, not an implementation.

**2. Check it is not already decided.**
Read `docs/architecture/decisions/`. A surprising share of "wouldn't it be
better if…" is answered there, often with the measurement that settled it. If it
is, link the ADR in the issue and say what is genuinely new about this case.

**3. Find the files yourself.**
Do not ask the user where something lives — grep. The point of the skill is that
this work happens once, when the issue is written, instead of every time someone
picks it up.

**4. Scope it to one thing.**
A PR that does two things cannot be reverted for one of them. If the request has
two halves, write two issues and note the ordering between them.

**5. Pick labels.**
State: exactly one of `agent-ready`, `needs-spec`, `human-only`.
Area: one of `area:srs`, `area:content`, `area:store`, `area:web`,
`area:modes`, `area:docs`, `area:ci`.
Kind, if it applies: `bug`, `content`.

Anything touching `src/repetita/srs/` deserves a warning in the body: it is the
one place where a bug costs months of study before anyone notices, and it is
under CODEOWNERS.

**6. File it.**

```bash
gh issue create --title "..." --body-file /tmp/issue.md \
  --label agent-ready --label area:srs
```

Show the user the body before filing if there is any doubt about scope.

## Worked example

Rough input: *"the multiple choice options are sometimes rubbish"*

That is not yet an issue — it is a symptom. Turned into one:

> **Title:** Reject `choice` items that cannot resolve three distractors
>
> **Goal:** `repetita validate --strict` exits non-zero when a `choice` item has
> fewer than three usable distractors, naming the item.
>
> **Files:** `src/repetita/content/validate.py` (the check),
> `tests/content/test_validate.py` (the test). Do not touch `src/repetita/srs/**`.
>
> **Test:** `pytest tests/content/test_validate.py::test_choice_needs_three_distractors`
>
> **Context an agent cannot infer:** weak distractors are not cosmetic. They
> inflate measured accuracy, and accuracy is what opens the new-material gate
> (`GATE_THRESHOLD`), so bad options cause the app to introduce material faster
> than the learner can absorb it. This is a scheduling bug wearing a UI costume.
> Distractors are precomputed, not chosen at runtime — see ADR-0005.
>
> Labels: `agent-ready`, `area:content`

Note what the transformation added: a testable threshold, the files, and the
reason the obvious fix ("just pick better words at runtime") is wrong.
