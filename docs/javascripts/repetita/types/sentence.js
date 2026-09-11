// A whole sentence, produced from a prompt in the language you already have.
//
// The prompt is what you are asked to say, so it is the line that is large. It
// is the one type where the question is entirely in the first language, which is
// worth showing rather than leaving to be inferred from which words look foreign.

import { el } from "../dom.js";

export const notetype = "sentence";

export function question(card) {
  return el("div", { class: "question" }, [
    el("p", { class: "ask", text: String(card.fields.prompt ?? "") }),
    el("p", { class: "instruction", text: "Say the whole sentence." }),
    card.fields.translation ? el("p", { class: "aside", text: card.fields.translation }) : null,
  ]);
}
