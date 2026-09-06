# Roadmap

Ordered by what unblocks what, not by appeal. Items marked **seam ready** have
their extension point already built, so the work is additive.

## Now — extracting the engine

- [x] Scaffolding, licences, CI
- [x] `core` types and protocols; `graders`; `sm2` + `leitner` backends
- [ ] Content model: pydantic models, YAML loader, JSON Schema generation
- [ ] Answer-leak quarantine, ported and generalised
- [ ] `store`: SQLite schema, migrations, `review_log`
- [ ] `roda import-hub`: notes, card state and 454 reviews out of the private app
- [ ] Parity test: same content, same history, same day, same queue

## Next

- [ ] `fsrs6` backend **(seam ready)**, run in parallel with `sm2` before switching
- [ ] Session policies: `daily`, then `cram`, `test`, `match`
- [ ] Precomputed distractors, `curated` > `paradigm` > `same_unit` > `frequency`
- [ ] Chapters: named study scopes, orthogonal to course units and tags
- [ ] Frontend split into ES modules, one per form; i18n catalogues

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

Field types already include `image`. A `picture` notetype with a `name_it` card
(show the image, type the word in the target language) needs a form module and an
importer for openly licensed images — Wikimedia Commons and Openverse both filter
by licence in their APIs, and iNaturalist has a great deal of CC0 wildlife.
The `license:` field on an image is mandatory and CI-enforced: a repository that
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
