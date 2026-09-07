# Third-party sources and their terms

The realistic legal risk in a project like this is **content attribution**, not
the software licence. This file is the record.

## Software

| Package | Licence | Why |
| :-- | :-- | :-- |
| [`fsrs`](https://github.com/open-spaced-repetition/py-fsrs) | MIT | The FSRS-6 scheduler. Runtime dependency is `typing-extensions` alone; the optimizer (which pulls torch) is an optional extra. |
| Flask, PyYAML, pydantic | BSD / MIT | Serving, content loading, validation. |

Read but **not** copied from, all AGPL-3.0: [Anki](https://github.com/ankitects/anki)
(the note→card model), [quenti](https://github.com/quenti-io/quenti) (per-scope
session state, precomputed distractors),
[LibreLingo](https://github.com/LibreLingo/LibreLingo) (course-as-directory,
generated exercises). Ideas are not copyrightable; their code is. None of it is
vendored here.

## Content sources that may be used

| Source | Licence | Conditions |
| :-- | :-- | :-- |
| [Tatoeba](https://tatoeba.org/en/downloads) sentence pairs | CC BY 2.0 FR | Attribution **per sentence**. Record the sentence id in the item's `attribution:` field. |
| [Common Voice](https://commonvoice.mozilla.org/) audio | CC0-1.0 | None. The only large, genuinely unencumbered Portuguese speech corpus. |
| [kaikki.org / wiktextract](https://kaikki.org/) | CC BY-SA + GFDL | Same terms as course content, so it composes. Prefer the English edition's Portuguese entries. |
| [Wikimedia Commons](https://commons.wikimedia.org/) images | varies, filterable | Per-file. Record `license:` and the file URL on the image field. |
| [Openverse](https://openverse.org/) images | varies, filterable | As above. |
| [iNaturalist](https://www.inaturalist.org/) research-grade photos | often CC0 / CC BY | As above. Good for the `animals` chapter. |
| [FrequencyWords](https://github.com/hermitdave/FrequencyWords) | per-file; code MIT | Frequency banding for distractors and curriculum ordering. Check the file. |
| [`wordfreq`](https://pypi.org/project/wordfreq/) | MIT | How common a word is, used to keep a distractor from being obviously rarer than the answer. Optional extra (63MB); the engine works without it. |
| *Português para principiantes*, UW-Madison | **CC BY-NC-SA 4.0** | **Structure only.** The NC clause is one-way incompatible with CC BY-SA, so no sentence, table or passage from it may be shipped as course content. Its teaching *sequence* — which grammar point precedes which — is a fact about the language, and following it is fine. Verified against the publisher's own metadata; see `reference/livro/LICENCA.md`. |

## Content sources that may NOT be used

| Source | Why not |
| :-- | :-- |
| Duolingo course content | Proprietary. No public format, no licence. |
| Language Transfer | Free to listen to; not licensed for redistribution or derived course data. |
| [Forvo](https://api.forvo.com/) | Paid, non-redistributable. Usable only as a user-supplied API key at runtime, never as a bundled asset. |
| [`mlconjug3`](https://github.com/Ars-Linguistica/mlconjug3) | MIT | **Does not import** as of 2026-09 — its pickled models were built against an older scikit-learn and raise `ModuleNotFoundError: No module named '_loss'` under 1.9. It also pulls 232MB. Conjugation paradigms are obtained instead from a shared-stem heuristic over the course's own answers, which needs no dependency and, on the first real corpus, produced the paradigm it was wanted for. |
| [`verbecc`](https://github.com/bretttolbert/verbecc) | Its conjugation data derives from Verbiste, which is GPL — incompatible with an MIT engine. Use `mlconjug3`. |
| Textbook sentences, commercial course material, in-copyright lyrics | Copyrighted, regardless of excerpt length. |

## Why this file exists

The predecessor of this project is a repository that can never be made public,
because 54 copyrighted illustrations are in its git history. Removing the files
in a later commit does not remove them from history. The cost of getting this
wrong is the whole repository, permanently — which is why image `license:` fields
are mandatory and CI-enforced rather than encouraged.
