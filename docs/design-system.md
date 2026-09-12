# The design system

`web/static/style.css` declares a design system at the top and then does not keep
to it. This page is the reference the stylesheet is measured against, and the one
rule that stops it drifting again.

**Read this before touching CSS.** It is short on purpose.

## The one rule

> **No literal may restate a token's value. If you need a value no token has, add
> the token.**

That is the whole of it. A literal that happens to equal a token is the drift:
`rgba(47, 93, 80, 0.25)` *was* `--accent` in light mode, which is why it stayed
dark green when dark mode turned the accent to mint. The colour was not wrong
when it was written; it stopped being right when something else moved and it
could not follow.

## The tokens

Every one of these is in use. Where a token exists, use it.

### Type

A single scale. `--t-lg` and up belong to the Study card and nothing else.

| token | value | for |
| --- | --- | --- |
| `--t-xs` | `0.78rem` | counts, chips, timestamps, field hints |
| `--t-sm` | `0.9rem` | the working size of every list and control |
| `--t-md` | `1rem` | body text |
| `--t-lg` | `1.2rem` | the assembled sentence in a word bank |
| `--t-xl` | `1.6rem` | the question |
| `--t-2xl` | `2rem` | a question that is one word |

`--t-lg` and `--t-xl` were declared at `1.35rem` and `1.9rem` and used by
**nothing**, while the Study card set its own `1.2 / 1.6 / 2`. A token that
describes nothing on screen is how a parallel scale starts, so these now describe
what is actually there.

### Space

| token | value |
| --- | --- |
| `--s-1` | `0.25rem` |
| `--s-2` | `0.5rem` |
| `--s-3` | `1rem` |
| `--s-4` | `1.75rem` |

`--s-3` and `--t-md` are both `1rem`, which matters when replacing a literal:
`gap: 1rem` is `--s-3` and `font-size: 1rem` is `--t-md`. **The property decides,
not the value.**

### Radius

`--radius: 8px`. Two values are deliberately not it and need no token: `999px`
for a pill and `50%` for a circle.

### Colour

`--bg` `--fg` `--muted` `--line` `--accent` `--pass` `--fail` `--warn`, each
redefined under `@media (prefers-color-scheme: dark)`.

Two washes of the accent, so that a selected surface follows the theme:
`--accent-wash` (18%) and `--accent-pick` (25%), both `color-mix`ed from
`--accent` so there is one definition rather than two that drift.

### How well something is known

`--m-untouched` `--m-working` `--m-declared` `--m-earned`. One encoding, three
tabs — Study's progress, Design's topic dots, Manage's row badges all mean the
same thing by the same colour, because they are the same question.

`--m-declared` is deliberately not `--m-earned`: *"I know this"* is a claim the
learner made, a mature card is evidence the scheduler gathered. **They never
share a fill.** They did, in dark mode, for as long as `--accent` and `--pass`
were both `#7fbfa8` — separated only by a hatch.

## What is still wrong

Named rather than fixed, because fixing it is a refactor with real regression
risk and this file is not a rewrite. Take one when you are next in the area.

* **Fifteen separate definitions of the bordered text input** — `.cfield-input`,
  `.mfield-input`, `.munit-input`, `.msearch`, `.mchip`, `.ctype`, `.cset*`,
  `.mband-pick`, `.cpaste-box`, `.mcompose-box`, `input.typein`, `.ctab`, `.tab`,
  `button`, `.waiting-link`. They should be one class plus modifiers.
* **Nine words for "this one is selected"**: `.on` (6), `.going` (3), `.picked`
  (2), `.chosen` (2), `.staged`, `.receiving`, `.over`, `.open`, `.coming`. Some
  of these genuinely mean different things — `.going` is *staged for removal* and
  `.coming` is *staged to come back* — but `.on` / `.picked` / `.chosen` are one
  idea under three names.
* **Two naming conventions.** Create and Manage are prefixed (`.c*` 67 rules,
  `.m*` 152). Study and Design are not (163 unprefixed), and their generic names
  collide: `.bar` is the page header, Design's share-bars, *and* the plan banner.
* **A parallel `em` type scale** — `0.85em` (13), `0.9em` (6), `0.8em` (5). These
  are relative to their parent rather than to the root, which is a different
  intent from the `--t-*` scale, so they are not simply literals to replace.
  Decide what they mean before tokenising them.
* **`.mastery` / `.mark` / `.m-*`** are shared concepts sitting inside Manage's
  namespace, and the four-state scale has two vocabularies in one 60-line
  section: classes say `untouched / started / getting-there / done`, tokens say
  `m-untouched / m-working / m-declared / m-earned`.

## Checking it

There is no linter for this yet. The audit that produced the numbers above is a
dozen lines of Python over the stylesheet with comments stripped; the two
questions worth asking are *"is any token unused?"* and *"does any literal
restate a token's value in that token's own role?"*. Both should answer zero.
