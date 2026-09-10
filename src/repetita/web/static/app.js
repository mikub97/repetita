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

function verdict(card, result, next) {
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
      // Offered here as well as before answering, because getting it right is
      // often the moment you realise you never needed to be asked at all.
      //
      // Only after a pass. Declaring "I know this" straight after a miss
      // contradicts the evidence just recorded, and would make the button a way
      // out of a card you have demonstrably not learnt. A near-miss still gets
      // the offer: HARD is a pass (ADR-0002), so a missing accent does not cost
      // it -- which is the same line the grader already draws.
      result.passed
        ? el("button", {
            class: "quiet",
            type: "button",
            text: "I know this",
            title: "Take it out of the queue. Your answer stays recorded.",
            onclick: () => declareKnown(card, { requeue: false }),
          })
        : null,
    ]),
  ]);

  clear(stage).append(node);
  // "Next" first, so it is what has focus and what Enter reaches. The other
  // button retires a card and should stay something you aim at deliberately.
  // The verdict is where a wrong answer key is discovered -- before it, the
  // learner has not been shown the answer to disagree with.
  clear(stage).append(node, asideRow(card, { answered: true }));
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
    verdict(card, result, showNext);
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
  clear(stage).append(mode.render(card, (answer) => submit(card, answer)), asideRow(card));
  document.getElementById("left").textContent = `${queue.length} left`;
}

// The reasons, as the server's codes with the labels a learner reads. The codes
// are the contract -- `store/reports.py` refuses one it does not know -- and the
// sentences are UI text, which is why they live here and not in Python.
const REASONS = {
  also_correct: "My answer was right too",
  wrong_answer: "The expected answer is wrong",
  ambiguous: "The question is ambiguous",
  typo: "There is a typo",
  bad_translation: "The translation is wrong",
  bad_options: "The options are bad",
  other: "Something else",
};

// Which reasons make sense depends on whether the answer has been seen yet: you
// cannot call an answer key wrong before you have been shown it, and "mine was
// right too" needs an answer of yours for the server to attach.
const BEFORE = ["ambiguous", "typo", "bad_translation", "other"];
const AFTER = ["also_correct", "wrong_answer", "ambiguous", "bad_translation", "other"];

// Controls that take a card out of rotation.
//
// Understated on purpose, and in one row rather than two: they should not sit
// where a thumb lands on the way to answering. There is no keyboard shortcut for
// the same reason -- the predecessor had one and removed it after a stray
// keystroke retired an item. Reporting has more claim to that caution than "I
// know this" does, because it also makes work for someone.
function asideRow(card, { answered = false } = {}) {
  const row = el("div", { class: "row aside-row" }, [
    answered
      ? null
      : el("button", {
          class: "quiet",
          type: "button",
          text: "I know this",
          title: "Take it out of the queue. You can undo it right after.",
          onclick: () => declareKnown(card),
        }),
    el("button", {
      class: "quiet",
      type: "button",
      text: "Something's wrong",
      title: "Report this exercise as broken. You can undo it right after.",
      // Opening the picker does not post. One tap to open and one to send costs
      // a tap and buys not keeping six buttons permanently beside a question.
      onclick: () => row.replaceWith(reportPicker(card, { answered })),
    }),
  ]);
  return row;
}

function reportPicker(card, { answered }) {
  const note = el("input", {
    class: "typein",
    type: "text",
    autocomplete: "off",
    placeholder: "anything to add? (optional)",
  });
  const codes = answered ? AFTER : BEFORE;
  // Only offered where a choice was actually served -- there are no options to
  // complain about otherwise.
  if (!answered && card.form === "choice") codes.splice(codes.length - 1, 0, "bad_options");

  return el("div", { class: "card reasons" }, [
    el("p", { class: "muted", text: "What is wrong with it?" }),
    el(
      "div",
      { class: "options" },
      codes.map((code) =>
        el("button", {
          class: "option",
          type: "button",
          text: REASONS[code],
          onclick: () => reportCard(card, code, note.value, { answered }),
        }),
      ),
    ),
    note,
  ]);
}

async function reportCard(card, reason, note, { answered }) {
  try {
    const result = await api("/api/report", {
      method: "POST",
      body: JSON.stringify({ card_id: card.id, reason, note, day: today() }),
    });
    document.getElementById("owed").textContent = result.owed;
    clear(stage).append(
      el("div", { class: "card" }, [
        el("p", { class: "ask", text: "Reported." }),
        el("p", { class: "muted", text: "Out of the queue until you fix it." }),
        el("div", { class: "row" }, [
          el("button", {
            class: "quiet",
            type: "button",
            text: "Undo",
            onclick: async () => {
              const undone = await api("/api/report", {
                method: "POST",
                body: JSON.stringify({ card_id: card.id, undo: true, day: today() }),
              });
              document.getElementById("owed").textContent = undone.owed;
              // Only put it back if it never left. After a verdict the card has
              // already been answered and is out of the queue on its own terms;
              // pushing it back would re-ask it in the same session, which is
              // the thing `bury_siblings` exists to prevent.
              if (!answered) queue.unshift(card);
              showNext();
            },
          }),
          el("button", { class: "primary", type: "button", text: "Next", onclick: showNext }),
        ]),
      ]),
    );
  } catch (error) {
    // Not queued offline. `flushPending` replays answers only, and a report
    // replayed twice is two rows in a work queue -- saying so is honester than
    // dropping it silently.
    status.textContent = error.offline
      ? "that needs a connection"
      : `could not do that (${error.message})`;
  }
}

async function declareKnown(card, { requeue = true } = {}) {
  try {
    const result = await api("/api/known", {
      method: "POST",
      body: JSON.stringify({ card_id: card.id, day: today() }),
    });
    document.getElementById("owed").textContent = result.owed;
    // The undo lives here rather than in a settings screen, because this is the
    // only moment the learner knows which card they meant.
    clear(stage).append(
      el("div", { class: "card" }, [
        el("p", { class: "ask", text: "Out of the queue." }),
        el("p", {
          class: "muted",
          text: requeue
            ? "Marked as known rather than measured."
            : "Your answer is still recorded. The card is out of the queue.",
        }),
        el("div", { class: "row" }, [
          el("button", {
            class: "quiet",
            type: "button",
            text: "Undo",
            onclick: async () => {
              const undone = await api("/api/known", {
                method: "POST",
                body: JSON.stringify({ card_id: card.id, undo: true, day: today() }),
              });
              document.getElementById("owed").textContent = undone.owed;
              // Only when the card was never answered. Undoing after an answer
              // must not re-ask it: it has already been graded and scheduled,
              // and putting it back would collect a second answer for one
              // meeting -- which is the thing sibling burying exists to prevent.
              if (requeue) queue.unshift(card);
              showNext();
            },
          }),
          el("button", { class: "primary", type: "button", text: "Next", onclick: showNext }),
        ]),
      ]),
    );
  } catch (error) {
    status.textContent = error.offline
      ? "that needs a connection"
      : `could not do that (${error.message})`;
  }
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

// The designer asks for a fresh queue after changing which plan is active. An
// event rather than an exported function because the two modules otherwise know
// nothing about each other, and a session loop that could be driven from
// elsewhere is a session loop with two places to look when it misbehaves.
document.addEventListener("repetita:restudy", () => {
  queue = [];
  load();
});

sync().finally(load);
