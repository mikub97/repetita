# Repetita — design review, and what a set should mean

**Reviewed:** 2026-09-11, against `812effd` (`fix(create): let go of unsaved work, on purpose (#63)`).
**Method:** read `src/repetita/web/` (templates, `style.css`, the five JS modules), `store/`, ADR-0001 to
ADR-0010, `courses/pt-br-from-pl-private/`; queried `data/repetita.db` read-only; walked the running app
at `localhost:5115/pt/` through Study, Design, Create and Manage.
**Not touched:** nothing in the repository was written. This is a specification, to be implemented by
whoever holds the branch.

A note on the moving target: three commits landed (#61, #62, #63) during the four hours this review took.
Everything asserted below was re-verified against `812effd` at the end. Line numbers may have moved since.

---

## 1. The complaint, diagnosed

> *"the sets are chaotical and I don't have a proper way to manage the sets and exercises I added with agent"*

The chaos is not a styling problem, and a design pass over the Manage tab will not fix it. It has three
causes, all of them in the data rather than the CSS, and all three are visible in a single screenshot of
the board.

### 1.1 No set has a name

All 30 live units have `title = '{}'`. Not one `unit.yaml` exists anywhere in
`courses/pt-br-from-pl-private/units/` — the public demo course has them
(`courses/pt-br-from-pl/units/01-cumprimentos/unit.yaml`, `title: {pl: Powitania, en: Greetings}`), the
course you actually study does not. So `unit.title?.en || unit.title?.pl || unit.id` (`manage.js:346`)
falls through to the slug on every column, and the board is thirty slugs, most of them truncated by
`text-overflow: ellipsis` at about 22 characters: `gram-demonstrativos…`, `gram-presente-irreg…`,
`licao-2026-09-06-ge…`.

This is the cheapest fix in this document and probably the largest single improvement. It is also a
content fix, not a code fix — but the code should make it impossible to end up here again (§4.1).

### 1.2 Exercise names are derived from the answer, and the answers repeat

`labels.derive()` takes the name from the answer when a note has no `label`, and `label_custom` is set on
0 of 757 notes — so every name on the board is derived. For vocabulary that reads well. For grammar it
collapses:

| Set | What the rows are called |
| :-- | :-- |
| `gram-atras-passado-ainda` (10) | `passada`, `atrás`, `atrás`, `ainda`, `ainda`, `ainda`, `ainda`, `atrás`, `passado`, `ainda` |
| `gram-contracoes-eque` (17) | three rows called `é que`, two called `ainda`, one called `Ainda` |
| `licao-2026-09-06-genero` (20) | **twenty rows called `o`** |

The disambiguator that distinguishes them is the tail of the note id, rendered as a two-digit grey number
right-aligned in the row (`manage.js:240–267, 303–305`): `06`, `07`, `08`, `09`. It carries no meaning to
a reader. In `licao-2026-09-06-genero` the right-hand column happens to show the *cue* (`cinema`,
`problema`, `sistema`) which is genuinely informative — but that is the family/variant mechanism firing by
luck, not a decision.

`docs/labels.md` says repeated names are deliberate, and for the study loop that is right. On a management
screen it is not: the board's job is to let you find one exercise among 757, and twenty rows called `o` is
the opposite of that. These are two different jobs for one field, which is why §4.2 proposes a second one
rather than changing `label`.

### 1.3 The set is the only grouping offered, and it is not the grouping you think in

The board groups by `unit` and nothing else. There is one free-text search box (`manage.js:681–691`,
matching label + question + answer + id + tags) and no filter, no sort, no secondary grouping.

Meanwhile the material already carries four other axes, all populated, none of them reachable from Manage:

| Axis | Coverage | Values |
| :-- | :-- | :-- |
| `track` | 757 / 757 | gramatica 475, vocabulario 262, fala 15, musica 5 |
| `level` | 757 / 757 | A2 563, A1 118, B1 76 |
| `topic` | 626 / 757 | 19 values — perfeito 98, imperfeito 76, tempo 55, … |
| `source` | 200 / 757 | licao-setembro |
| `lesson` (date column) | 251 / 757 | **no UI anywhere** |

And a sixth grouping is encoded in the slugs themselves, which you clearly invented for exactly this
purpose and which nothing in the app reads:

`gram-` 11 sets / 317 exercises · `licao-` 8 / 152 · `vocab-` 7 / 218 · `texto-` 2 / 50 · `fala-` 1 / 15 ·
`musica-` 1 / 5

So: you built a taxonomy, twice — once as facets in `facets.yaml`, once as a naming convention — and the
management screen shows neither. That is the chaos. Thirty equal-width columns in alphabetical slug order,
where `gram-*` fills the first screen and the lesson you did yesterday is somewhere below the fold.

### 1.4 A lesson does not survive as a thing

`licao-2026-09-10-irregulares.yaml` contains 53 notes. They are in the unit `gram-preterito-perfeito`.
`licao-2026-09-10.yaml` contains 4 more, elsewhere again. So a file — which is what an agent writes per
lesson — does not correspond to a set, and a lesson's material scatters across thematic sets by design.

That is a reasonable design. What is missing is the other view: `notes.lesson` (a date, set on 251 notes)
is the field that would answer *"what came in on the 10th, and where did it go?"*, and it has no input and
no display in any tab (`create.js:194` forwards it; nothing ever sets it; nothing shows it). Right now the
only way to answer that question is to read the YAML.

This will get worse, not better, with Anki import: a 1,200-card deck landing in this board with no way to
see it as one arrival is the same failure at ten times the size.

---

## 2. The two paths are invisible, and the database already knows the difference

You asked that it be clear there are two ways to manage the app — by agent, and by hand. The store was
built for exactly this and the interface throws it away.

**What the database records**

| Field | Meaning | Reaches the browser? | Shown? |
| :-- | :-- | :-- | :-- |
| `notes.origin` | source file, `''` for written in the app | **yes** — `api.py:_note_json`, `"origin": note.origin` | **no** — no JS reads it |
| `notes.edited_at` | non-NULL ⇒ changed by hand here | no | no |
| `notes.archived_at` | gone from the source, never deleted | no (used as a filter) | no |
| `notes.label_custom` | the name was typed, not derived | no | no |
| `units.edited_at` / `archived_at` | same, for sets | no | no |
| `notes.lesson` | the day it entered | yes | no |

`origin` is the sharp one: it is serialised, sent over the wire on every `/api/material` call, and dropped
on the floor. `create.js:openSet` (117–126) does not even copy it into its row objects. ADR-0010 names this
field as the thing that says who wrote an exercise — *"whatever the app creates or changes must record that
it did"* — and no screen says it.

**Why it does not hurt yet, and why that is the reason to fix it now.** Today: 757 notes, every one from a
file, `edited_at` set on zero, `origin` empty on zero, `pending_changes` empty, `material_drafts` empty.
The population is uniform, so nothing is ambiguous. The first time you fix a typo in Manage and then ask an
agent to regenerate that lesson, it stops being uniform — and the screen that should tell you what happened
has no vocabulary for it. Build the distinction while the answer is still trivially checkable.

**Three consequences, today**

1. **There is no "what changed" view.** After an agent run you cannot ask: what is new, what did it update,
   what did it archive, what did it refuse. `sync()` returns a `SyncReport` with exactly those four numbers
   (ADR-0006) and the web UI never shows one — it is a CLI return value.
2. **Archived material leaves the world.** `archived_at` is a filter, never a payload. Remove a set of 76
   exercises and there is no screen, count, or route that can reach it again. `store/material.py:114`
   declares a `restore` change kind; `grep restore static/*.js` returns nothing. The safety property
   ADR-0006 fought for — *archived, never deleted* — is invisible to the person it protects, which means in
   practice it reads as deletion.
3. **The two commit models are split across the two set-level operations.** Manage stages and applies on
   Confirm (`"· changes here wait for Confirm"`); Create saves immediately (`"· saves straight away"`). Both
   are defensible and both are labelled. But *renaming* a set lives on the immediate side (Create) and
   *removing* a set lives on the staged side (Manage), so the two operations on a set's existence are in
   different tabs under opposite rules. That is the seam a new user falls through, and the Italian course
   will have a new user.

---

## 3. What a set should mean

Four concepts already exist and three of them are unexposed. The fix is not a fifth concept. It is to say,
once, what each of the four is for, and then make each visible in exactly one place.

| Concept | The question it answers | Cardinality | Where it should live |
| :-- | :-- | :-- | :-- |
| **set** (`unit`) | *where does this exercise live?* | exactly one | the shelf: Manage's columns, Create's picker |
| **topic / tag** | *what is it about?* | many | Design's planning; a filter on Manage |
| **lesson** | *when did it arrive, and with what?* | one date, optional | an arrivals view; a badge |
| **origin / edited** | *who wrote it — a file, an agent, or me?* | one | a mark on every row |

Three rules follow, and they are worth stating in the repo because they will be asked again for Italian and
again for Anki:

1. **A set is a shelf, not a subject.** It is where a thing is filed, chosen by whoever filed it; it is not
   a claim about what the thing teaches. Subject is what tags are for. This is already true in the data —
   `gram-preterito-perfeito` holds notes from three different files and two different lessons — and saying
   it out loud settles most of the "should this be a set or a tag" questions for good.
2. **Every set has a name a person wrote.** A slug is an identifier, not a name. If no name exists the
   interface should say so and offer to take one, rather than silently printing the id (§4.1).
3. **Every exercise says where it came from.** Not as an audit trail — as one mark in the row, so that
   "material I wrote", "material an agent wrote", and "material I changed after an agent wrote it" are three
   visibly different things. This is the requirement you actually stated, and it is one field that is
   already in the payload.

---

## 4. The fixes, in priority order

### Tier 1 — these are what "chaotic" means

**4.1 Give sets names, and make namelessness visible.**
Three parts. *Content:* write `unit.yaml` with a `title` for all 30 units — an agent task, half an hour.
*Import:* nothing needed; the loader already reads it. *Interface:* where `manage.js:346` falls back to
`unit.id`, render the id in the muted, monospaced style used for identifiers plus an inline "name this set"
affordance, so a nameless set looks unfinished rather than named-in-slug. Renaming a set currently requires
going to the Create tab; the name should be editable where the set is looked at.

**4.2 Stop the board printing twenty rows called `o`.**
Do not change `label` — the study loop and `docs/labels.md` depend on it. Instead: when a name repeats
inside a column, the row should show the *cue* or a truncated prompt beside it instead of the numeric id
tail. The mechanism exists — `mnote-tell` already renders a second string in exactly that slot, and it is
what makes `licao-2026-09-06-genero` legible in the one place it happens to fire. Make it the rule rather
than the accident, and drop the two-digit id suffix from the board entirely; it identifies nothing to a
human. (`manage.js:240–267`, `noteRow` 269–309.)

**4.3 Group and filter the board by something other than the set.**
Minimum: a control that groups columns by `track` (4 values, covers everything) or by the slug prefix, and
a filter by `level`, `topic` and state. This turns thirty ungrouped columns into four labelled shelves and
is the difference between a wall and a library. The facet data is already in `note_facets` (2,340 rows) and
already has an endpoint — `GET /api/catalogue?group_by=<axis>` — which Design calls with `topic` hardcoded
as a string literal (`designer.js:156`) even though `axes` is fetched and never read (`designer.js:61, 160`).
Most of the server work is done.

**4.4 Show provenance on every row.**
One mark, three states, derived from fields that already exist: *from a file* (`origin` non-empty,
`edited_at` null) · *written here* (`origin` empty) · *changed here* (`edited_at` non-null). Add `edited_at`
to `_note_json` — `origin` is already there. Put the mark in the row and the detail in the editor pane
("from `licao-2026-09-10-vocab.yaml`, edited by you on 11 Sept"). Filterable, so "show me everything I
touched" is one click. This is the two-paths requirement, and it is perhaps a day's work.

### Tier 2 — the shape of the work you do weekly

**4.5 An arrivals view, keyed on `lesson`.**
"What came in on 2026-09-10" — 81 exercises, which sets they landed in, how many you have since edited.
The column holds three dates and nothing else: 2026-09-01 (72), 2026-09-06 (98), 2026-09-10 (81).
This is the missing half of §1.4, it is the natural home for a `SyncReport` after an agent run, and it is
the view that makes an Anki import comprehensible rather than terrifying. It needs `lesson` in the payload
and an input in Create; the column is already populated on a third of the material.

**4.6 Make archived material reachable.**
An "archived" filter or view, and a restore control wired to the `restore` change kind that already exists
in the store. Until this exists, removing a set is indistinguishable from deleting it, and a person who has
learned that will never use the feature.

**4.7 Move set rename next to set removal.**
Both operations on a set's existence should be in the same place under the same commit rule. My suggestion
is Manage (where you look at sets), staged like everything else there — but either answer is better than
the current split. Whichever you choose, say it in the ADR: the commit-model split between the tabs is
deliberate and documented, its consequences for set-level operations are not.

**4.8 Make the columns say how much is left.**
A column shows 13 rows of 76 and scrolls internally with no scrollbar until you hover, and scrolling over a
column scrolls the column while scrolling over the gutter scrolls the page. Either cap the column with an
explicit "…and 63 more" row, or let the column grow and the page scroll. Right now the board silently hides
five sixths of your largest set.

### Tier 3 — polish, once the above settles

- Study is a 40rem card floating in the vertical middle of an empty 1512px screen, with ~280px of dead
  space above it. It is the tab that works, and it is also mostly nothing. Worth a look after the rest.
- Create opens on "New set…" even when you arrived from a set you were reading.
- `.mboard` at phone width becomes a horizontal scroll-snap carousel of 85vw columns — a reasonable idea,
  but with 30 unnamed columns it is 30 swipes.

---

## 5. The design system

There is a real design system declared at the top of `style.css` — five type sizes, four spacing steps, one
radius, thirteen colours, and a four-value scale for how well something is known that Study, Design and
Manage genuinely share. The intent is good and the comments explaining it are better than most. The file
does not keep to it.

**The numbers** (full audit available; `style.css` is now 1586 lines):

- **41 places restate a token's exact value as a literal** — `0.9rem` for `--t-sm` ×5, `8px` for `--radius`
  ×5, `0.5rem` for `--s-2` ×18, `1rem` for `--s-3` ×6, and `rgba(47, 93, 80, α)` — which is literally
  `--accent` in light mode — ×3 (L254, L855, L931).
- **~55 more are near-misses**: a parallel undeclared type scale of `0.8 / 0.85 / 0.9 / 0.95 / 1.2 / 1.6`
  (36 literal font sizes against 14 token references), and six spellings of the small radius
  (`8px`, `6px`, `0.4rem`, `5px`, `4px`, `3px`) across 24 declarations.
- **`--t-md`, `--t-lg` and `--t-xl` are declared and never used.** Three of the five type tokens are dead
  while 36 literals do their job.
- **Three contiguous blocks — L336 to L731, covering the Study card, the shared chrome, the tabs and
  design-my-lessons — contain zero spacing, type or radius token references.** Colour tokens are used
  there; geometry tokens are not. That is where the system stops.

**The one that is a bug, not untidiness:** the three `rgba(47, 93, 80, α)` literals do not follow the dark
override. In dark mode `--accent` becomes `#7fbfa8` (mint), so a selected `.cform` or a picked `.mnote`
gets a mint border and a dark-green fill. Also in dark mode `--accent` and `--pass` are set to the *same*
value, which makes `--m-earned` and `--m-declared` — two states the tokens' own comment insists "mean
opposite things, so they never share a fill" — the same colour, separated only by a hatch.

**Duplication.** The same component is defined independently per tab: eight separate definitions of the
bordered text input, four of the list row (`.crow` and `.mnote` are identical but for `cursor`), three
track-and-fill progress bars, two of the small toggle button, four separate declarations of the drag
affordance. Six words are in use for "this one is selected": `.on`, `.picked`, `.chosen`, `.over`,
`.staged`, `.receiving`.

**Naming.** Create and Manage are cleanly prefixed (`.c*`, `.m*`); Study and Design have no prefix and their
generic names (`.bar`, `.preview`, `.row`, `.tab`) collide with the prefixed ones — `.bar` is the page
header, Design's share-bars, and the plan banner. `.mastery`/`.mark`/`.m-*` are shared concepts sitting
inside Manage's namespace. Two vocabularies describe one four-state scale in the same 60-line section:
classes say `untouched / started / getting-there / done`, tokens say `m-untouched / m-working / m-declared /
m-earned`.

**What I would do.** Not a rewrite. One reference file in the repo — `docs/design-system.md` or a comment
block the agent is told to read before touching CSS — stating the tokens, the six duplicated components
that should become one each, and one rule: *no literal may restate a token's value; if a value is needed
that no token has, add the token*. Then one mechanical pass replacing the 41 exact duplicates, which is safe
and reviewable. The near-misses can wait; the `rgba(47,93,80)` three cannot, because they are wrong in dark
mode today.

---

## 6. Three things to check, which I could not settle from reading

1. **The Design tab may be printing answers.** `designer.js:9–13` states the catalogue returns counts and
   labels only, never ids, *because about a quarter of ids are literally the answer*. But the plan preview's
   "for example" chips print labels — and labels are derived from answers. On the running app that section
   reads: `vimos · ouvi · vi · fizemos · lemos · leram · ouviu · ouviram · ouvimos · fizeram · pôde · viu`,
   which is the answer set of the material about to be studied, shown on a screen you visit before studying
   it. ADR-0008 exempts Manage from the no-answers rule deliberately; Design is not Manage. Either the
   exemption should be extended and written down, or the preview should show cues rather than labels.
2. **`ARCHITECTURE.md` still describes the pre-ADR-0006 world.** It says *"The content tables are a cache,
   wiped and rebuilt from the course files on every load"* — which ADR-0006 explicitly supersedes and which
   ADR-0010 builds further on. The ADR is right and the architecture document contradicts it, and it is the
   first file `CLAUDE.md` tells a new contributor to read.
3. **One note, one card.** 757 notes produce 757 cards; 639 of them are `gap`. The note→card→form model is
   the centrepiece of ADR-0001 and your material barely exercises it. Not a defect — but if the
   multi-direction idea is a selling point of the public project, this course is not yet the demonstration
   of it, and that is worth knowing before the Italian course is shaped the same way.

---

## 7. What comes next

The critique is done; the other three pieces you asked for depend on decisions in §3 and §4 that are yours
to make. In the order I would take them:

1. **Settle §3** — the four concepts and the three rules, as a short ADR. Everything else references it, and
   the Italian course and the Anki import both need it to exist before they start.
2. **Mock up the Manage board** against that model: named sets, grouped by track, provenance marks, an
   arrivals view. This is the screen the complaint is about, and it is worth drawing before it is built.
3. **The design-system reference file**, plus the mechanical pass over the 41 exact duplicates and the
   three dark-mode colour bugs.
4. **The copy pass** — last, because half of it is naming the things §3 defines. Note that Manage says
   "exercise" while the API says "note", says "set" while everything under the hood says "unit", and the
   pending-changes drawer prints the raw internal kinds (`unit`, `label`) beside rows whose visible text
   uses the other vocabulary. One glossary fixes all of it.

Say which of these to start on.
