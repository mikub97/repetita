// The session loop: fetch a queue, show a card, post an answer, show the verdict.
//
// Native ES modules, no bundler and no build step. That is a contributor-facing
// choice rather than an omission: a form is one file under `modes/`, and adding
// one should not mean learning this project's toolchain first.

import { api, flushPending, queueAnswer, readPending } from "./api.js";
import { el, clear } from "./dom.js";
import * as choice from "./modes/choice.js";
import * as typein from "./modes/typein.js";
import * as wordbank from "./modes/wordbank.js";
import * as flashcard from "./modes/flashcard.js";

// Registered by name rather than imported from a path built at runtime, so the
// set of forms this client can render is visible in one place.
const MODES = Object.fromEntries(
  [choice, typein, wordbank, flashcard].map((mode) => [mode.form, mode]),
);

const stage = document.getElementById("stage");
const status = document.getElementById("status");

// The learner's calendar day, which is not necessarily the server's. Sending it
// is what keeps an evening session in one timezone from being filed under
// another's tomorrow.
const today = () => {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

let queue = [];
let started = 0;

// `owed` and `answered_today` come back from /api/answer as well as /api/state,
// so the tiles move with every answer without a second round trip.
function counters(state) {
  document.getElementById("owed").textContent = state.owed;
  document.getElementById("answered").textContent = state.answered_today;
  if (state.target !== undefined) document.getElementById("target").textContent = state.target;
  document.getElementById("left").textContent = `${queue.length} left`;
}

function verdict(result, next) {
  const shown = Object.entries(result.reveal).map(([name, value]) =>
    el("p", { class: "aside" }, [
      el("span", { class: "label", text: name }),
      el("span", { text: Array.isArray(value) ? value.join(" · ") : String(value) }),
    ]),
  );

  const diff = result.diff.length
    ? el(
        "p",
        { class: "diff" },
        result.diff.map((token) =>
          el("span", {
            class: `tok ${token.kind}`,
            text: token.given ?? token.expected ?? "—",
          }),
        ),
      )
    : null;

  const node = el("div", { class: `card verdict ${result.passed ? "pass" : "fail"}` }, [
    el("p", { class: "grade-label", text: result.passed ? "Correct" : "Not quite" }),
    diff,
    el("p", { class: "ask", text: result.answers.join(" / ") }),
    ...shown,
    el("p", { class: "muted", text: `next in ${result.interval} d (${result.due ?? "—"})` }),
    el("div", { class: "row" }, [
      el("button", { class: "primary", type: "button", text: "Next", onclick: next }),
    ]),
  ]);

  clear(stage).append(node);
  node.querySelector("button").focus();
}

async function submit(card, answer) {
  status.textContent = "";
  try {
    const result = await api("/api/answer", {
      method: "POST",
      body: JSON.stringify({
        card_id: card.id,
        day: today(),
        ms: Date.now() - started,
        ...answer,
      }),
    });
    counters(result);
    verdict(result, showNext);
  } catch (error) {
    if (!error.offline) {
      status.textContent = `could not save that answer (${error.message})`;
      return;
    }
    // Kept, not lost. The card moves on so the session keeps its rhythm; the
    // verdict is the one thing that cannot be shown, because only the server
    // knows whether the answer was right.
    const waiting = queueAnswer({
      card_id: card.id,
      day: today(),
      ms: Date.now() - started,
      ...answer,
    });
    showOffline(waiting);
    showNext();
  }
}

function showOffline(waiting, sent = 0, rejected = 0) {
  const parts = [];
  if (sent) parts.push(`${sent} sent`);
  if (waiting) parts.push(`${waiting} answer${waiting === 1 ? "" : "s"} saved here`);
  if (rejected) parts.push(`${rejected} refused by the server`);
  status.textContent = parts.join(" · ");
}

async function sync() {
  if (!readPending().length) return;
  const { sent, left, rejected } = await flushPending();
  showOffline(left, sent, rejected);
  if (sent) load();
}

function showNext() {
  const card = queue.shift();
  if (!card) {
    load();
    return;
  }
  const mode = MODES[card.form];
  if (!mode) {
    // A form the server can serialise but this client cannot draw. Skipping is
    // better than a blank screen, and better than guessing at a renderer.
    status.textContent = `no renderer for form "${card.form}", skipped`;
    showNext();
    return;
  }
  started = Date.now();
  clear(stage).append(mode.render(card, (answer) => submit(card, answer)));
  document.getElementById("left").textContent = `${queue.length} left`;
}

async function load() {
  try {
    const [state, session] = await Promise.all([
      api(`/api/state?day=${today()}`),
      api(`/api/session?day=${today()}`),
    ]);
    queue = session.cards;
    counters(state);
    if (!queue.length) {
      clear(stage).append(
        el("div", { class: "card" }, [
          el("p", { class: "ask", text: "Nothing due." }),
          el("p", {
            class: "muted",
            text: state.done ? "Today is done." : "Come back when something falls due.",
          }),
        ]),
      );
      return;
    }
    if (session.consolidating) status.textContent = "extra practice — the plan is done";
    showNext();
  } catch (error) {
    clear(stage).append(el("p", { class: "muted", text: `could not load (${error.message})` }));
  }
}

// Anything saved while offline goes first, so the counters the session starts
// with already include it.
window.addEventListener("online", sync);
sync().finally(load);
