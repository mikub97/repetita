# Exercise types

<!--
  GENERATED FILE -- do not edit.
  Written by scripts/gen_types_doc.py from the registry in
  src/repetita/content/notetypes/. Change a declaration there; CI checks this
  page matches.
-->

An **exercise type** says two things: which fields an exercise may carry, and
which cards those fields make. It says nothing about any language — these are
shapes of knowledge, and the Portuguese lives in the courses.

There are 6 built in, and a course may declare its own — see
[Adding a type](#adding-a-type) at the bottom. Every example below is **rendered
by the app's own code**, so what you see is what a learner sees. Nothing here is
graded.

Two words that recur:

* **before / after** — whether a field is on screen while the question is open.
  A field shown *before* must never contain the answer; that rule is what
  `repetita validate` enforces, and an exercise that breaks it is dropped from
  the pool rather than warned about.
* **form** — how a card is asked: typed, a word bank, a multiple choice, or a
  flashcard you rate yourself. A card's grader decides which are possible; an
  exercise may choose among them ([ADR-0010][adr10]).

[adr10]: architecture/decisions/0010-material-can-be-written-in-the-app.md

## `gap`

One word missing from a sentence in the target language -- the core drill.

| field | type | | shown |
| --- | --- | --- | --- |
| `prompt` | text | **required** | with the question |
| `answers` | text_list | **required** | after answering |
| `cue` | text |  | with the question |
| `hint` | text |  | with the question |
| `translation` | text |  | with the question |
| `options` | text_list |  | after answering |
| `distractors` | text_list |  | after answering |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **1 card** per exercise:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `fill` | `prompt` · `cue` | `answers` | `typed` | choice, typein |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-gap-fill&quot;, &quot;notetype&quot;: &quot;gap&quot;, &quot;template&quot;: &quot;fill&quot;, &quot;form&quot;: &quot;choice&quot;, &quot;ask&quot;: [&quot;prompt&quot;, &quot;cue&quot;], &quot;fields&quot;: {&quot;prompt&quot;: &quot;Amanhã eu ___ estudar português.&quot;, &quot;cue&quot;: &quot;ir — futuro simples, eu&quot;, &quot;translation&quot;: &quot;Jutro będę uczyć się portugalskiego.&quot;}, &quot;options&quot;: [&quot;vou&quot;, &quot;vai&quot;, &quot;vamos&quot;]}"></div>

## `phrase`

Something to say out loud, judged by the learner.

The one place where self-assessment is honest, because nothing else can hear it
yet.

| field | type | | shown |
| --- | --- | --- | --- |
| `situation` | text | **required** | with the question |
| `translation` | text |  | with the question |
| `target` | text | **required** | after answering |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **1 card** per exercise:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `say` | `situation` · `translation` | `target` | `self` | flashcard |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-phrase-say&quot;, &quot;notetype&quot;: &quot;phrase&quot;, &quot;template&quot;: &quot;say&quot;, &quot;form&quot;: &quot;flashcard&quot;, &quot;ask&quot;: [&quot;situation&quot;, &quot;translation&quot;], &quot;fields&quot;: {&quot;situation&quot;: &quot;Chegas ao treino e cumprimentas o grupo.&quot;, &quot;translation&quot;: &quot;Dzień dobry wszystkim!&quot;}}"></div>

## `picture`

Show a picture, name it in the target language.

| field | type | | shown |
| --- | --- | --- | --- |
| `image` | image | **required** | with the question |
| `l2` | text | **required** | after answering |
| `l1` | text |  | with the question |
| `distractors` | text_list |  | after answering |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **1 card** per exercise:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `name_it` *(needs an image)* | `image` | `l2` | `typed` | typein, choice |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-picture-name_it&quot;, &quot;notetype&quot;: &quot;picture&quot;, &quot;template&quot;: &quot;name_it&quot;, &quot;form&quot;: &quot;typein&quot;, &quot;ask&quot;: [&quot;image&quot;], &quot;fields&quot;: {&quot;image&quot;: &quot;data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNjAnIGhlaWdodD0nMTUwJz48cmVjdCB3aWR0aD0nMjYwJyBoZWlnaHQ9JzE1MCcgcng9JzYnIGZpbGw9JyNlOGU0ZGMnLz48cGF0aCBkPSdNNDAgMTEwIEw5NSA1NSBMMTM1IDk1IEwxNjUgNzAgTDIyMCAxMTAgWicgZmlsbD0nI2I5YjJhNScvPjxjaXJjbGUgY3g9JzE4NScgY3k9JzQ1JyByPScxNicgZmlsbD0nI2Q4ZDJjNicvPjwvc3ZnPg==&quot;, &quot;l1&quot;: &quot;krajobraz&quot;}}"></div>

## `sentence`

A whole sentence produced from an L1 prompt.

| field | type | | shown |
| --- | --- | --- | --- |
| `prompt` | text | **required** | with the question |
| `answers` | text_list | **required** | after answering |
| `translation` | text |  | with the question |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **1 card** per exercise:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `produce` | `prompt` | `answers` | `sentence` | wordbank, typein |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-sentence-produce&quot;, &quot;notetype&quot;: &quot;sentence&quot;, &quot;template&quot;: &quot;produce&quot;, &quot;form&quot;: &quot;wordbank&quot;, &quot;ask&quot;: [&quot;prompt&quot;], &quot;fields&quot;: {&quot;prompt&quot;: &quot;Jak się masz?&quot;}, &quot;tokens&quot;: [&quot;Como&quot;, &quot;vai?&quot;]}"></div>

## `transform`

A sentence plus an instruction saying what to change about it.

| field | type | | shown |
| --- | --- | --- | --- |
| `prompt` | text | **required** | with the question |
| `instruction` | text | **required** | with the question |
| `answers` | text_list | **required** | after answering |
| `translation` | text |  | with the question |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **1 card** per exercise:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `apply` | `prompt` · `instruction` | `answers` | `sentence` | wordbank, typein |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-transform-apply&quot;, &quot;notetype&quot;: &quot;transform&quot;, &quot;template&quot;: &quot;apply&quot;, &quot;form&quot;: &quot;wordbank&quot;, &quot;ask&quot;: [&quot;prompt&quot;, &quot;instruction&quot;], &quot;fields&quot;: {&quot;prompt&quot;: &quot;Eu moro em Salvador.&quot;, &quot;instruction&quot;: &quot;no pretérito imperfeito&quot;}, &quot;tokens&quot;: [&quot;Eu&quot;, &quot;Salvador.&quot;, &quot;em&quot;, &quot;morava&quot;]}"></div>

## `vocab`

A word, tested in as many directions as the note supports.

Where the note->card multiplication earns its keep: one entry, three cards.

Note that `l1` is the prompt for `produce` and the answer for `recognize`. A
field's `visibility` is the type's default; a card's own `expect` always wins,
which `NoteType.visible_before` handles.

| field | type | | shown |
| --- | --- | --- | --- |
| `l2` | text | **required** | after answering |
| `l1` | text | **required** | with the question |
| `pos` | text |  | with the question |
| `example_l2` | text |  | after answering |
| `example_l1` | text |  | with the question |
| `distractors` | text_list |  | after answering |
| `explain` | text |  | after answering |
| `audio` | audio |  | after answering |

It makes **3 cards** per exercise, scheduled separately:

| card | asks | answer | marked by | can be asked as |
| --- | --- | --- | --- | --- |
| `recognize` | `l2` | `l1` | `typed` | choice, typein |
| `produce` | `l1` | `l2` | `typed` | choice, typein, wordbank |
| `listen` *(needs audio)* | `audio` | `l2` | `typed` | typein |

<div class="demo" data-card="{&quot;id&quot;: &quot;demo-vocab-recognize&quot;, &quot;notetype&quot;: &quot;vocab&quot;, &quot;template&quot;: &quot;recognize&quot;, &quot;form&quot;: &quot;choice&quot;, &quot;ask&quot;: [&quot;l2&quot;], &quot;fields&quot;: {&quot;l2&quot;: &quot;a feira&quot;}, &quot;options&quot;: [&quot;targ&quot;]}"></div>

## Adding a type

A type is a declaration, and optionally a view. Nothing else.

Put the declaration in `courses/<your course>/notetypes.yaml`. It is merged over
the built-ins when the course loads, so **no Python is involved** and a name that
matches a built-in replaces it for that course:

```yaml
ditado:
  fields:
    audio:   {type: audio, required: true, visibility: before}
    answers: {type: text_list, required: true, visibility: after}
    hint:    {type: text}
  cards:
    write:   {ask: [audio], expect: answers, grader: typed, forms: [typein]}
```

`repetita validate` refuses a declaration that could not work, by name:

* a card that names a field the type does not have, in `ask`, `expect` or
  `requires`;
* a grader that does not exist — `typed`, `sentence`, `choice`, `self`;
* a form that grader cannot judge (a `typed` card asked as a flashcard would be
  marked against the learner's own rating instead);
* a type with no cards, or a card with no forms.

A broken type is dropped and reported rather than merged, so the notes using it
are not quarantined for a reason that is not their fault.

### Giving it a look of its own

Optional. Without one, a type is drawn by the generic renderer — the asked fields
largest first, the rest quieter — which is right for most exercises. Write one
when the type has a shape that deserves showing: `gap` marks its blank inside the
sentence, `transform` shows its instruction as a task rather than a second
sentence.

One file in `src/repetita/web/static/types/`, exporting two things:

```js
export const notetype = "ditado";

export function question(card) {
  return el("div", { class: "question" }, [ ... ]);
}
```

`card` is the payload the server builds for an open question. **It never contains
the answer**, and nothing in a view may reach for one.
