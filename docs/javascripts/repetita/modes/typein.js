// Type the answer.
//
// A form module renders a card and calls `submit` with what the learner did. It
// never decides whether that was right: it has not been told the answer, and
// asking the server is the only way to find out.

import { el } from "../dom.js";
import { question } from "../types/index.js";

export const form = "typein";

export function render(card, submit) {
  const input = el("input", {
    class: "typein",
    type: "text",
    autocomplete: "off",
    autocapitalize: "off",
    autocorrect: "off",
    spellcheck: "false",
  });

  const send = () => submit({ text: input.value });
  const node = el("div", { class: "card" }, [
    question(card),
    el("form", { class: "row", onsubmit: (e) => (e.preventDefault(), send()) }, [
      input,
      el("button", { class: "primary", type: "submit", text: "Check" }),
    ]),
  ]);

  queueMicrotask(() => input.focus());
  return node;
}
