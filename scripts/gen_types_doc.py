#!/usr/bin/env python3
"""
Write `docs/exercise-types.md` from the registry, with examples that run.

The same argument as `gen_schema_doc.py`: a hand-written list of six exercise
types and their fields would be wrong within a month and believed anyway. The
declarations are the documentation, so the page is read out of them.

The examples are not screenshots either. This copies the app's own renderers into
`docs/javascripts/repetita/` and the page renders each type with them, so a
picture of an exercise cannot drift from the exercise -- if a type's view changes,
the page changes with it.

    python scripts/gen_types_doc.py           # write the page and the assets
    python scripts/gen_types_doc.py --check   # fail if either is out of date (CI)
"""

from __future__ import annotations

import argparse
import filecmp
import html
import json
import shutil
import sys
from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repetita.content.notetypes import builtin  # noqa: E402
from repetita.core.forms import markable  # noqa: E402

#: Drawn, not photographed. Neither course has `picture` material, so there is no
#: licensed image to show -- and hotlinking one would put an unattributed image
#: in the documentation of a project whose rule about images is its strictest.
PLACEHOLDER = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='260' height='150'>"
    "<rect width='260' height='150' rx='6' fill='#e8e4dc'/>"
    "<path d='M40 110 L95 55 L135 95 L165 70 L220 110 Z' fill='#b9b2a5'/>"
    "<circle cx='185' cy='45' r='16' fill='#d8d2c6'/></svg>"
)

PAGE = ROOT / "docs" / "exercise-types.md"
STATIC = ROOT / "src" / "repetita" / "web" / "static"
ASSETS = ROOT / "docs" / "javascripts" / "repetita"

#: What gets copied for the live examples: the renderers and what they import.
COPIED = ("dom.js", "types", "modes")

#: One example per type. The sample course only has `vocab` and `sentence`
#: material, and a page about six types that can only show two is not a page
#: about six types. These are illustrations, not course content.
EXAMPLES: dict[str, dict[str, object]] = {
    "gap": {
        "prompt": "Amanhã eu ___ estudar português.",
        "cue": "ir — futuro simples, eu",
        "translation": "Jutro będę uczyć się portugalskiego.",
        "answers": ["vou"],
        "distractors": ["vai", "vamos"],
    },
    "sentence": {
        "prompt": "Jak się masz?",
        "answers": ["Como vai?"],
    },
    "transform": {
        "prompt": "Eu moro em Salvador.",
        "instruction": "no pretérito imperfeito",
        "answers": ["Eu morava em Salvador."],
    },
    "phrase": {
        "situation": "Chegas ao treino e cumprimentas o grupo.",
        "translation": "Dzień dobry wszystkim!",
        "target": "Bom dia a todos!",
    },
    "vocab": {
        "l2": "a feira",
        "l1": "targ",
        "example_l2": "Vou à feira no sábado.",
    },
    # A drawn placeholder rather than a photograph. There is no `picture`
    # material in either course, so there is no licensed image to show -- and
    # hotlinking one would put an unattributed image in the documentation of a
    # project whose rule about images is the strictest it has.
    "picture": {
        # Base64 rather than a literal SVG: the payload travels inside an HTML
        # attribute inside JSON, and a picture full of quotes ends that
        # attribute early.
        "image": "data:image/svg+xml;base64," + b64encode(PLACEHOLDER.encode()).decode(),
        "l1": "krajobraz",
        "l2": "a paisagem",
    },
}

HEAD = """\
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

There are {count} built in, and a course may declare its own — see
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
"""

TAIL = """
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
"""


def example(name: str) -> dict[str, object]:
    return EXAMPLES.get(name, {})


def demo_card(name: str, template: str) -> dict[str, object] | None:
    """A `public_card`-shaped payload for the page to render."""
    nt = builtin()[name]
    card = nt.cards[template]
    fields = example(name)
    if not fields:
        return None
    accepted = fields.get(card.expect) or []
    first = accepted[0] if isinstance(accepted, list) and accepted else str(accepted or "")
    shown = {f: fields[f] for f in nt.visible_before(template) if fields.get(f)}
    forms = [f for f in card.forms if f in markable(card.grader)]
    form = forms[0] if forms else "typein"
    payload: dict[str, object] = {
        "id": f"demo-{name}-{template}",
        "notetype": name,
        "template": template,
        "form": form,
        "ask": [f for f in card.ask if fields.get(f)],
        "fields": shown,
    }
    if form == "wordbank":
        payload["tokens"] = sorted(str(first).split())
    if form == "choice":
        wrong = [str(w) for w in (fields.get("distractors") or [])][:2]
        payload["options"] = [str(first), *wrong]
    return payload


def render() -> str:
    types = builtin()
    parts = [HEAD.format(count=len(types))]

    for name in sorted(types):
        nt = types[name]
        module = (ROOT / "src" / "repetita" / "content" / "notetypes" / f"{name}.py").read_text()
        doc = module.split('"""')[1].strip() if '"""' in module else ""
        parts.append(f"\n## `{name}`\n\n{doc}\n")

        parts.append("\n| field | type | | shown |\n| --- | --- | --- | --- |\n")
        for fname, spec in nt.fields.items():
            needed = "**required**" if spec.required else ""
            when = "with the question" if spec.visibility == "before" else "after answering"
            parts.append(f"| `{fname}` | {spec.type} | {needed} | {when} |\n")

        many = len(nt.cards) > 1
        parts.append(
            f"\nIt makes **{len(nt.cards)} card{'s' if many else ''}** per exercise"
            f"{', scheduled separately' if many else ''}:\n\n"
        )
        parts.append("| card | asks | answer | marked by | can be asked as |\n")
        parts.append("| --- | --- | --- | --- | --- |\n")
        for template, card in nt.cards.items():
            asks = " · ".join(f"`{a}`" for a in card.ask)
            can = [f for f in card.forms if f in markable(card.grader)]
            needs = " *(needs audio)*" if "audio" in card.requires else ""
            needs = " *(needs an image)*" if "image" in card.requires else needs
            parts.append(
                f"| `{template}`{needs} | {asks} | `{card.expect}` | `{card.grader}` "
                f"| {', '.join(can)} |\n"
            )

        payload = demo_card(name, next(iter(nt.cards)))
        if payload:
            # Escaped, because an example is arbitrary text and an apostrophe in
            # it would close the attribute it lives in.
            attr = html.escape(json.dumps(payload, ensure_ascii=False), quote=True)
            parts.append(f'\n<div class="demo" data-card="{attr}"></div>\n')
    parts.append(TAIL)
    return "".join(parts)


def copy_assets(check: bool = False) -> bool:
    """Put the renderers where the docs can load them. True if anything differs."""
    stale = False
    for item in COPIED:
        source, target = STATIC / item, ASSETS / item
        if source.is_dir():
            files = sorted(p.name for p in source.glob("*.js"))
            for named in files:
                stale |= _one(source / named, target / named, check)
            if not check:
                for extra in target.glob("*.js"):
                    if extra.name not in files:
                        extra.unlink()
        else:
            stale |= _one(source, target, check)
    return stale


def _one(source: Path, target: Path, check: bool) -> bool:
    if check:
        return not (target.is_file() and filecmp.cmp(source, target, shallow=False))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the page is out of date")
    args = ap.parse_args()

    want = render()
    if args.check:
        have = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if have != want or copy_assets(check=True):
            print(
                "docs/exercise-types.md or its copied renderers are out of date.\n"
                "An exercise type changed and the page did not. Run:\n"
                "    python scripts/gen_types_doc.py",
                file=sys.stderr,
            )
            return 1
        print("docs/exercise-types.md matches the registry")
        return 0

    PAGE.write_text(want, encoding="utf-8")
    copy_assets()
    print(f"wrote {PAGE.relative_to(ROOT)} and the renderers under {ASSETS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
