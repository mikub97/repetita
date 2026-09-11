// One word, asked in whichever direction this card is.
//
// A single word does not need a paragraph's worth of typography around it: it is
// the whole question, so it is the whole screen. The example sentence, where the
// note has one and the card may show it, sits under as context.

import { el } from "../dom.js";

export const notetype = "vocab";

export function question(card) {
  const asked = card.ask.map((name) => card.fields[name]).filter(Boolean);
  const rest = Object.entries(card.fields)
    .filter(([name, value]) => !card.ask.includes(name) && value)
    .map(([, value]) => value);

  return el("div", { class: "question" }, [
    el("p", { class: "ask word", text: asked.map(String).join(" · ") }),
    ...rest.map((value) => el("p", { class: "aside", text: String(value) })),
  ]);
}
