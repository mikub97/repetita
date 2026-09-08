# ADR-0005: The client never sees a card id

**Status:** accepted, 2026-09-07
**Context:** issue #17

## Context

Invariant 2 of `ARCHITECTURE.md` says an answer is not in the payload while its
question is open. The serialiser enforces it by field: `NoteType.visible_before`
composes which fields are the question and which is the answer, and nothing else
is sent.

An id is not a field, and it has to reach the client so an answer can be posted
back for it.

Card ids are authored from the material. A note teaching *obrigado* is called
`obrigado`, so its production card is `obrigado#produce`. The id **is** the
answer. Measured on the first real course imported into this engine: **167 of
676 cards, a quarter of the corpus.** Every one was answerable by reading the
network tab, and no field filter could have caught a single one.

## Decision

The client is given a random per-run token for each card. The server resolves the
token when the answer arrives. Neither the card id nor the note id appears in any
payload.

## Alternatives rejected

**Rename the ids.** Forbidden by CLAUDE.md rule 1 — an id is a scheduling key,
and renaming one silently deletes every learner's progress on that item. It also
only holds until the next contributor names a note after the word it teaches,
which is the obvious thing to do and should stay obvious.

**Hash the id.** Stateless and tempting, and wrong: a hash of a guessable input
is not opaque. The corpus is public, so anyone can hash every id once and build
the lookup table. Security that depends on nobody bothering is not a property.

**Encrypt the id with a per-process key.** Equivalent in effect to a random map
and harder to reason about. The map costs one dict of a few hundred entries.

## Consequences

* ~~Handles are per-run.~~ **Amended 2026-09-08: handles are persisted.**

  They were minted per run, justified as "a question open across a restart
  should make the client refetch anyway". Adding an offline answer queue made
  that costly. An answer given while offline is posted when the connection
  returns; if the server restarted in between, a per-run handle resolves to
  nothing and a **real answer is lost** — the one failure the queue exists to
  prevent.

  Nothing in the guarantee above depended on regeneration. A token is random and
  says nothing about the material whether it lives for an hour or a year, and
  the threat model is a learner reading the DOM, not one hoarding tokens. So
  handles live in a `card_handles` table, and an unknown handle still resolves
  to nothing — it is just no longer *every* handle after a restart.
* The payload key stays `id`, because the client only ever echoes it back. No
  client code changed, which is the point: this is a server-side property and
  should not be one the front end can get wrong.
* `note_id` is gone from payloads entirely. It shares the prefix, so it leaked
  the same answers, and nothing was using it.
* Anything added later that carries a card id to the client — a deep link, an
  error message, a debug endpoint — reopens this. The id is server-side data.
