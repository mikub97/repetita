// A sentence, and an instruction about what to change.
//
// Two things, and which is which matters: the instruction is the task and the
// sentence is the material. Shown as a chip above the sentence rather than as a
// second paragraph, because read as prose they blur into one long request.

import { el } from "../dom.js";

export const notetype = "transform";

export function question(card) {
  return el("div", { class: "question" }, [
    card.fields.instruction
      ? el("p", { class: "task", text: String(card.fields.instruction) })
      : null,
    el("p", { class: "ask", text: String(card.fields.prompt ?? "") }),
    card.fields.translation ? el("p", { class: "aside", text: card.fields.translation }) : null,
  ]);
}
