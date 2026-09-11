# Designing your lessons

The Study tab follows the course's own order. The Design tab is where you say
**what you want to get better at**, and see what that would actually mean before
committing to it.

## Your material, on the left

Every topic in your course, as a bar: how much material there is, and how much
of it you have started. A hatched stripe is material you marked as known rather
than learned — worth seeing separately, because it is the part your statistics
cannot vouch for.

Click or drag a topic into the plan on the right.

## Your plan, on the right

An ordered list. **The top of the list gets the biggest share of new material**,
and the share shrinks down the list — so a plan is a statement of priority
rather than a filter. Drag to reorder; the percentages move as you do.

Three knobs sit under it, written as sentences rather than as variable names:

- *One new card after every 3 you already owe.*
- *Sessions of about 40 cards.*
- *Lean towards producing the word, which is harder and sticks better.*

Each one is a real tuning constant with a row in [Tuning](../tuning.md). The rest
stay out of the interface rather than becoming dials nobody understands.

## What you would practise

The preview is the point of the tab. It shows the mix your list produces —

```
tempo        ████████████░░░░  13
gramatica    ████░░░░░░░░░░░░   7

for example
 sexta-feira   quinta-feira   dia da semana   hoje   alguma   amanhã
```

— and a handful of names for the exercises it would actually introduce, so that
a plan is a claim you can check rather than one you have to trust.

!!! note "Those names are answers, and the sample is shuffled"

    A name is usually the answer to its exercise, so reading the preview means
    seeing some answers to material you are about to be asked. The sample is
    shuffled on every call, which breaks the link between the order you read and
    the order you are served — a mitigation, not a fix, and accepted on purpose
    because the material is yours. The reasoning is in
    [ADR-0008](../architecture/decisions/0008-the-management-surface-sees-everything.md).

**Practise this plan** starts a session under it. It needs at least one priority
— a plan with an empty list is not a plan, and the button says so rather than
quietly serving the ordinary session.

## What a plan does not do

- It does not hide anything. Owed cards from these topics come first; the rest
  of your backlog is still on the Study tab, unchanged.
- It does not change how answers count. An answer given under a plan is an
  ordinary answer.
- It does not rewrite history when you change your mind. Every answer records
  which *revision* of the plan produced it, so "did this plan help?" stays a
  question the data can answer ([ADR-0007](../architecture/decisions/0007-a-plan-is-data.md)).

## Something's off here

The button beside **Practise this plan** files an issue about how the material is
organised — two tags for one subject, a topic that should be split, a set that
is really two. It is the same queue an agent reads when you ask it to tidy the
tags; see [Tagging](../tagging.md).
