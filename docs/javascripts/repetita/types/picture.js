// Name what you can see.
//
// The image is the question. Everything else is a caption, and a caption that
// gives the word away would make the exercise pointless -- which is why `l1` is
// declared as shown-before and the answer never is.

import { el } from "../dom.js";

export const notetype = "picture";

export function question(card) {
  return el("div", { class: "question" }, [
    card.fields.image
      ? el("img", { class: "picture", src: String(card.fields.image), alt: "" })
      : el("p", { class: "muted", text: "(no image)" }),
    card.fields.l1 ? el("p", { class: "ask", text: String(card.fields.l1) }) : null,
  ]);
}
