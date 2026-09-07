// Rebuild the sentence from shuffled tokens.
//
// The server ships `card.tokens` already shuffled and never the assembled
// sentence -- reassembling it is the entire exercise, so the sentence in the
// payload would be the answer in the DOM. What goes back is the text the learner
// built, graded server-side like any other typed answer.

import { el, question } from "../dom.js";

export const form = "wordbank";

export function render(card, submit) {
  const built = [];
  const line = el("p", { class: "built" });
  const bank = el("div", { class: "bank" });

  const redraw = () => {
    line.textContent = built.map((t) => t.word).join(" ");
  };

  card.tokens.forEach((word, index) => {
    const chip = el("button", {
      class: "chip",
      type: "button",
      text: word,
      onclick: () => {
        const at = built.findIndex((t) => t.index === index);
        if (at === -1) {
          built.push({ index, word });
          chip.classList.add("used");
        } else {
          built.splice(at, 1);
          chip.classList.remove("used");
        }
        redraw();
      },
    });
    bank.append(chip);
  });

  return el("div", { class: "card" }, [
    question(card),
    line,
    bank,
    el("div", { class: "row" }, [
      el("button", {
        class: "primary",
        type: "button",
        text: "Check",
        onclick: () => submit({ text: built.map((t) => t.word).join(" ") }),
      }),
    ]),
  ]);
}
