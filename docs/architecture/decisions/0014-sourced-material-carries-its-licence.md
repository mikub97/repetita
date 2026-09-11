# ADR-0014: Sourced material carries its licence

**Status:** accepted, 2026-09-11
**Context:** [ADR-0006](0006-the-database-owns-the-material.md),
[ADR-0009](0009-material-is-captured-before-it-is-shaped.md),
[ADR-0011](0011-the-rules-become-mechanisms.md), `courses/CLAUDE.md`,
`docs/THIRD-PARTY.md`

## Context

Every exercise in this repository was typed by a person. Three large bodies of
openly licensed material exist that could feed that work and none of them is
reachable from here: Wiktionary's definitions, inflection tables and IPA
(via wiktextract/kaikki.org), Lingua Libre's recordings by native speakers, and
OpenSubtitles-derived frequency lists (hermitdave/FrequencyWords).

`docs/THIRD-PARTY.md` has listed all three as *permitted* since it was written.
Permitted is not the same as reachable, and the gap has stayed open because the
hard part was never the parsing.

**The hard part is that `courses/` is CC BY-SA 4.0 and `emit.py` writes a course
directory.** The predecessor of this project is a repository that can never be
made public, because 54 copyrighted illustrations are in its git history and
deleting a file in a later commit does not remove it from history. That is the
one mistake in this project that cannot be undone, which is why licensing sits
outside the four rules in `CLAUDE.md` — it is not a rule about care, it is the
condition for the repository existing.

A reader that hands back a string is therefore the wrong shape. By the time the
string reaches an author it has lost the only thing that decides whether it may
be used, and the loss is invisible: an unattributed sentence looks exactly like
one that needed no attribution.

## Decision

### 1. `sources/` is a new directory, not part of `importers/`

`importers/__init__.py` says what that package is in one line: *"Readers for
other systems' data. One module per predecessor."* It is about **our own
history** — the hub database this engine was extracted from, imported once, with
ADR-0004 governing what may be corrected. Its defining concern is that the
history is real and must not be recomputed.

External material is a different thing with a different governing concern:
nobody's study history is at stake, and the licence is. Sharing a directory would
put two packages with opposite invariants behind one name, and the next reader
would have to discover which one they were in.

### 2. Every item carries its provenance, and the licence is per item

One shared `Provenance` dataclass in `sources/`, on every record every reader
returns: the source, the licence, the attribution string, the item's own URL, and
the date the underlying dump was retrieved.

Per item rather than per source, because for one of the three it genuinely
varies — a Lingua Libre recording is CC BY-SA 4.0 or CC0 depending on the
recording, and a category that is 95% one and 5% the other is exactly the case
where a per-source constant is wrong in the 5% nobody checks. For the other two
the licence is structurally per dump, and carrying it per item costs one pointer.

**Three licence states, not two**, using SPDX's own tokens. Two states cannot
express what these sources actually say, and the evidence is the first one we
looked at — kaikki.org states, verbatim:

> This data is made available under the same licenses as Wiktionary - both
> CC-BY-SA and GFDL.

No version numbers. Recording that as `CC-BY-SA-4.0` invents a version the source
did not state; recording it as unknown throws away the facts that terms exist and
are copyleft. Both available answers under a two-state model are wrong, in
opposite directions.

| state | `spdx` | example |
| :-- | :-- | :-- |
| a listed identifier or expression | `CC-BY-SA-4.0`, `CC0-1.0` | a Commons file |
| stated, but matching no listed id | `LicenseRef-wiktionary-cc-by-sa-gfdl` | kaikki, above |
| not asserted | `NOASSERTION` | a FrequencyWords row |

The sentinel is SPDX's `NOASSERTION` rather than `None` because it survives
serialisation still saying what it means. A `None` becomes a NULL column, a JSON
`null` or a missing key, and at that point "nobody stated a licence" is
indistinguishable from "this field was never populated" — which is precisely the
confusion that lets unlicensed material through.

`License.unknown()` takes a mandatory `why`. An item with no licence cannot be
constructed without a sentence recording what was looked at and what was missing,
because that sentence is the whole of what a person will have when they decide
whether the item may be used.

### 3. What the rule forbids, and the name it is given

The property is **`redistributable`**, and it is a floor rather than a permit:
false means no decision can be made at all and the material must not leave this
machine; true means only that the question is answerable.

It is deliberately not called `exportable`, which was the first name tried.
`exportable` reads as permission, and the counter-example is already documented
one file away: `docs/THIRD-PARTY.md` records *Português para principiantes* as
CC BY-NC-SA 4.0 — a licence that is perfectly well known and from which no
sentence may ship, because NC is one-way incompatible with CC BY-SA. A property
called `exportable` returning true for that material is a trap that would read as
correct in review. `export` is also `store/export.py`'s word for writing a course
directory, and `sources/` may not import `store` or borrow its vocabulary.

The enforcement is a function, not a sentence — ADR-0011's argument applied to
this rule:

```python
def require_redistributable(items):   # raises on the first item with no stated terms
def redistributable_only(items):      # filters, silently, by design
```

Any future path that hands sourced material to something which writes files or
serves bytes routes through the first of those. That makes the rule one greppable
call instead of a paragraph somebody has to remember.

### 4. These are libraries, and nothing is wired

No write to the database, no endpoint, no UI, no change to `courses/`. Each
package returns its own dataclasses and constructs no `Note` or `Card`. **The
mapping from those dataclasses to note fields is documented in prose and
deliberately not implemented.**

That mapping is the integration and it is a separate decision, for two reasons.
It is where the licence translation happens — `Provenance` to a course's
`attribution:` — which is the highest-consequence code in the whole feature and
deserves its own review rather than arriving as a detail of a parser. And ADR-0009
already decided that material is captured before it is shaped: which gloss of six
becomes a `vocab` note's `l1` is an editorial judgement, and a reader that made
it silently would be making it for every word at once.

### 5. Stdlib only, and no new extra

`sources/` may import the standard library and nothing else — not even `core/`.
This is stricter than the layer table asks for, and the reason is not that
`core/` has nothing useful. It is that `core/` is the vocabulary of scheduling;
nothing in it models a lexical entry, a recording or a frequency row, and the
first import would be the gradient by which `core/` eventually learns what a
dictionary is. `core/`'s "stdlib only" is the load-bearing row of that table.
It also forces the `Provenance` to `LicenseSpec` mapping to be written explicitly
at the wiring site, rather than something `Provenance` helpfully does to itself.

**No `[project.optional-dependencies]` extra is added, because nothing needs
one.** Streaming JSONL is `json` and `gzip`; HTTP is `urllib.request`; the
lexicon index is `sqlite3`; compressing stored records is `zlib`. The brief this
was built from assumed extras would be required, and that assumption did not
survive writing the dependency list down.

Declaring one anyway, as a place to put things later, was considered and
rejected: `pip install repetita[lexicon]` that installs nothing teaches the user
something false that they have no way to check, and this repository has already
paid for that once — `mkdocs.yml`'s own header records that the `docs` extra was
*"declared in pyproject's `docs` extra long before anything used them"*. An
extras name is a public API, and a name shipped is a name supported.

The practical benefit is that the requirement "the engine imports and every
existing test passes with the extras not installed" becomes true by construction
instead of by a CI job. It is checked by a test that walks the AST of every
module under `sources/` and asserts every imported root is in
`sys.stdlib_module_names` — which also makes "no new base runtime dependency" a
fact the suite verifies rather than a thing a reviewer remembers to look for.

If the lexicon build later proves CPU-bound on `json.loads` — it plausibly is,
over tens of millions of lines — `orjson` is the one dependency worth having, and
it goes in behind the pattern `content/distractors.py` already uses: a
function-local `try: import … except ImportError:`, where absence changes how long
a build takes and never what comes out.

### 6. The `wordfreq` question, settled

`wordfreq` is already an optional dependency, in the `distractors` extra. A
second frequency source needs a reason, and the conclusion is that **it answers a
different question**.

`wordfreq` answers *"is this candidate wrong answer about as common as the right
one?"*. In `content/distractors.py` its value is bucketed to an integer —
`gap = int(abs(frequency(key) - want) / FREQ_BUCKET)` with `FREQ_BUCKET = 1.0` —
and used as the **third of five sort keys**, on the **first token only**, ordering
options within a tier and never deciding which options are eligible. Its own
docstring says absence changes "polish, not correctness". It is a plausibility
tie-breaker, and it is a good one.

A ranked list answers *"what is word number 500 in this language, and what are
the 499 before it?"*. That is a curriculum you walk down, and `wordfreq` cannot
answer it: it exposes a smooth probability for a word you already have, not an
ordering with an identity, a length and a provenance. Extending the existing use
was the alternative considered, and it fails on that — there is no word-list to
extend, only a function.

Two pieces of supporting evidence that this is a gap rather than a duplication:
`docs/THIRD-PARTY.md` has listed FrequencyWords for *"frequency banding for
distractors and curriculum ordering"* since before either existed, and the
`frequency` distractor tier promised in `ROADMAP.md` and in the `db.py` column
comment was never built — `SOURCES` is `("curated", "morphology", "same_unit")`.

The two are complementary and both stay. Nothing about `distractors` changes
here.

## Consequences

* **A reader may return material that can never be published, and says so.**
  Every FrequencyWords row comes back `redistributable == False`, because the
  repository's `LICENSE` file is MIT and GitHub reports the repository as MIT,
  while its README says *"MIT License for code"* and *"CC-by-sa-4.0 for
  content"*. Those two statements are not reconciled at source and are not
  reconciled here. This costs nothing in practice — ranking and banding consume
  the numbers locally — and it is the honest reading.
* **Verification has a date on it, and so does its absence.** Every external
  fact in these packages' documentation was checked against the primary source
  on 2026-09-11 and is recorded with that date. Two findings would have been got
  wrong from memory: kaikki's per-language downloads are **deprecated** in favour
  of per-edition raw extracts, and Lingua Libre is mid-migration to Wikimedia
  Commons, with its old wiki now a locked archive and Commons announcing itself
  as the single source of truth. Where something could not be confirmed the docs
  say so **in those words** rather than stating it confidently.
* **`sources/` is bound by the language rule more strictly than the engine is.**
  `scripts/check_language_neutral.py` parses the source and skips docstrings,
  which is right for the engine — a docstring explaining the rule is the rule
  being explained. Here every byte of every file is checked, including comments
  and docstrings, because these readers are driven entirely by language codes the
  caller supplies and a language name in an example is a language name that gets
  copied into code.
* **The layer table gains a row**, `sources/ | stdlib only | anything else in
  repetita`. Without it the rule is only in this file.
* **The enforcement half of the licence rule does not exist yet**, and cannot
  until something is wired. `sources/` can supply the fact and the filter; the
  chokepoint has to be in the code that writes a course. `require_redistributable`
  is named here so that the future change has somewhere to route through and a
  reviewer has something to ask for.
* If a fourth source is added, it adds a subpackage and reuses `Provenance`.
  `Provenance.source` is a plain string rather than an enum precisely so that
  doing this does not mean editing shared code.
