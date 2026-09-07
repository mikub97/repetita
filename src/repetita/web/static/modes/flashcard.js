// Say it out loud, then say how it went.
//
// The only card the machine cannot judge, so the learner does -- the `self`
// grader validates the rating and nothing else. The answer is revealed by the
// verdict, after the rating is submitted, because it is not in this payload and
// there is no second endpoint that would hand it over early. Rating before
// seeing is the honest order anyway: being shown the answer first is how
// self-assessment turns into "yes, I knew that".

import { el, question } from "../dom.js";

export const form = "flashcard";

// The four grades every scheduler here speaks (core.types.Rating).
const GRADES = [
  { value: 1, label: "Again" },
  { value: 2, label: "Hard" },
  { value: 3, label: "Good" },
  { value: 4, label: "Easy" },
];

export function render(card, submit) {
  return el("div", { class: "card" }, [
    question(card),
    el("p", { class: "muted", text: "Say it, then rate your recall." }),
    el(
      "div",
      { class: "row" },
      GRADES.map((grade) =>
        el("button", {
          class: "grade",
          type: "button",
          text: grade.label,
          onclick: () => submit({ choice: String(grade.value) }),
        }),
      ),
    ),
  ]);
}
