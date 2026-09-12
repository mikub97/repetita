# Roadmap

Ordered by what unblocks what, not by appeal. Items marked **seam ready** have
their extension point already built, so the work is additive.

## Done — the engine runs

An end-to-end study session works on a real imported corpus: 676 notes, 676
cards, 454 reviews of history, served on localhost.

- [x] Scaffolding, licences, CI, agent working context
- [x] `core` types and protocols; `graders`; `sm2` and `leitner` backends
- [x] Content model: pydantic models, YAML loader, note→card expansion
- [x] Answer-leak quarantine, generalised to run per (note, card)
- [x] `store`: SQLite schema, migrations, the append-only `review_log`
- [x] `policies.daily`: due, gated introductions woven in, consolidation last
- [x] `web`: `/api/state`, `/api/session`, `/api/answer`, one page, no bundler
- [x] `repetita import-hub`, emitting a course directory and the study history
- [x] Parity test: same content, same history, same day, same queue
- [x] `fsrs6` backend — **registered, not switched to.** The switch is an
      evidence decision on a real review log, not a default change.
- [x] `presenters.ladder`: a never-answered card is asked in a recognition form
- [x] **Accounts.** `user_id` was in the schema and unused; it points at a row
      now, four of them, with a login, a switcher and per-person progress
      everywhere (ADR-0016). This entry used to sit under *Deliberately not
      doing* on the grounds that "adding the auth layer now would be building
      for a user who does not exist" — which was true, and stopped being true.

## Next

- [ ] Precomputed distractors, `curated` > `paradigm` > `same_unit` > `frequency`.
      This unblocks the `choice` form, which the web layer refuses to serve
      today because a multiple choice must put the answer on screen beside its
      distractors and there are none to put there.
- [ ] Compare `sm2` and `fsrs6` on the owner's own review log, then decide
- [ ] Session policies beyond `daily`: `cram`, `test`, `match`
- [ ] Chapters: named study scopes, orthogonal to course units and tags
- [ ] i18n catalogues; the UI is English-only and the engine is language-neutral
- [ ] JSON Schema generated from the pydantic models, for editor autocomplete
- [ ] Audio: `audio:` as text plus a voice, synthesised at build time to
      content-addressed files, so no binaries enter the content repo

## Good large first contributions

### Speaking and pronunciation **(seam ready)**

`Response` already carries `audio`, `mime` and `ms`, and `Grader.accepts`
declares which channels a grader reads. What remains:

* a `speaking` form using `MediaRecorder` (native API, no bundler);
* `POST /api/answer` accepting `multipart/form-data`;
* an `asr` grader with a swappable backend — `whisper.cpp` or `mlx-whisper`
  locally;
* scoring by edit distance over **phonemes**, not letters, with a threshold
  rather than a binary verdict.

Two things to know before starting, because each costs a day to rediscover:

* Browser recording requires HTTPS or localhost. Behind `tailscale serve` this is
  satisfied; over a bare LAN IP it is not.
* **ASR output must not go straight to the scheduler as a rating.** An accent is
  not the same thing as not knowing the word. `asr` grades softly by default
  (HARD rather than AGAIN) with its own threshold in course configuration.

### Image exercises **(seam ready)**

The `picture` note type exists, with an image field type and a `name_it` card
(show the image, type the word in the target language). What remains is a form
module and an importer for openly licensed images — Wikimedia Commons and
Openverse both filter by licence in their APIs, and iNaturalist has a great deal
of CC0 wildlife. The `license:` field on an image is mandatory: a repository that
accumulates unlicensed images can never be made public.

### Cold-start prior on initial stability

FSRS starts every card at the same stability regardless of what the card is —
`universidade` and `saudade` are indistinguishable to it. Half-life regression's
one good idea was to regress initial half-life on item features. Bolting that on
as a prior over `S₀` only, leaving FSRS's update rules untouched, is cheap:

```
S₀ = w[G-1] × f(frequency, cognate_with_L1, item_type, length)
```

For a course with a fixed language pair the signal is strong. This is a research
contribution as much as an engineering one.

## Deliberately not doing

* **A front-end framework.** The absence of a build step is a contributor-facing
  choice. Revisit when a form genuinely needs shared client state — not before.
* **Serving `choice` without real distractors.** Shipping the answer beside three
  filler options to make a mode appear is not a mode.
