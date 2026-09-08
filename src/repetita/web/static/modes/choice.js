// Pick the answer from a short list.
//
// The one form whose payload legitimately contains the answer: a multiple choice
// IS the answer among others, and knowing which is the exercise. The options
// arrive already shuffled from the server (`serialize.public_card`), so position
// carries nothing and this module must not reorder them.
//
// They also arrive already filtered: `distractors.py` refuses to offer a choice
// at all unless there are real wrong answers to put beside the right one, since
// options that can be eliminated without knowing anything inflate accuracy --
// and accuracy is what opens the new-material gate.

import { el, question } from "../dom.js";

export const form = "choice";

export function render(card, submit) {
  const options = el("div", { class: "options" });

  // One shot: after a pick the buttons go inert, so a double tap cannot post a
  // second answer for a card that is already being graded.
  let answered = false;

  for (const option of card.options ?? []) {
    const button = el("button", {
      class: "option",
      type: "button",
      text: option,
      onclick: () => {
        if (answered) return;
        answered = true;
        for (const other of options.children) other.disabled = true;
        button.classList.add("picked");
        submit({ choice: option });
      },
    });
    options.append(button);
  }

  return el("div", { class: "card" }, [question(card), options]);
}
