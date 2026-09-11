// One word missing from a sentence.
//
// The sentence is the exercise, so it is the thing that is large, and the blank
// inside it is marked rather than left as three underscores in a wall of text.
// The cue -- "morar — imperfeito, eu" -- is the grammatical instruction and
// reads as a label beside it, not as a second sentence.

import { el } from "../dom.js";

export const notetype = "gap";

//: How a gap is written in the course files, and has been since the first one.
const BLANK = "___";

export function question(card) {
  const prompt = String(card.fields.prompt ?? "");
  const pieces = prompt.split(BLANK);
  const line = el("p", { class: "ask" });
  pieces.forEach((piece, i) => {
    line.append(document.createTextNode(piece));
    if (i < pieces.length - 1) line.append(el("span", { class: "blank", text: BLANK }));
  });

  return el("div", { class: "question" }, [
    line,
    card.fields.cue ? el("p", { class: "cue", text: card.fields.cue }) : null,
    card.fields.hint ? el("p", { class: "hint", text: card.fields.hint }) : null,
    card.fields.translation
      ? el("p", { class: "aside", text: card.fields.translation })
      : null,
  ]);
}
