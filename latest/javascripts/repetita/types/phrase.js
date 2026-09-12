// Something to say out loud, given a situation.
//
// The situation is a scene, not a question -- "you arrive at training and greet
// the group" -- so it reads as one, and the translation sits under it as the
// thing you are aiming to produce.

import { el } from "../dom.js";

export const notetype = "phrase";

export function question(card) {
  return el("div", { class: "question" }, [
    card.fields.situation ? el("p", { class: "scene", text: String(card.fields.situation) }) : null,
    card.fields.translation ? el("p", { class: "ask", text: String(card.fields.translation) }) : null,
  ]);
}
