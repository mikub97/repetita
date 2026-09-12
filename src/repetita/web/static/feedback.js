// "Tell me what you think" — from anywhere, about anything.
//
// In the header rather than on a tab, because the moment worth capturing is
// whenever it happens, and a comment you have to navigate to is a comment
// nobody leaves. It records which screen you were on and which course, so a
// note reading "this is confusing" still means something three days later.
//
// Deliberately not the same thing as "Something's wrong" on a card, which is a
// claim about one exercise and suspends it. This is about the app.

import { api } from "./api.js";
import { el, fill, toast } from "./dom.js";

const mount = document.getElementById("say");
let open = false;
let sending = false;

function where() {
  // The tab as a person would name it, not the internal view id.
  return document.body.dataset.tab || "study";
}

function render() {
  if (!mount) return;
  fill(
    mount,
    el("button", {
      class: `say-open${open ? " on" : ""}`,
      type: "button",
      title: "Tell me what you think — about anything, from anywhere",
      onclick: () => {
        open = !open;
        render();
        if (open) mount.querySelector("textarea")?.focus();
      },
    }, [el("span", { text: "Say something" })]),
    open ? box() : null,
  );
}

function box() {
  const field = el("textarea", {
    class: "say-box",
    rows: 6,
    placeholder:
      "Anything at all — what confused you, what you liked, what you expected to happen.\n\nWritten in whatever language you think in.",
    onkeydown: (event) => {
      // Ctrl/Cmd+Enter sends, because the box is for paragraphs and Enter has
      // to keep making new lines.
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") send(field);
    },
  });

  return el("div", { class: "say" }, [
    field,
    el("div", { class: "say-row" }, [
      el("span", {
        class: "muted say-hint",
        text: `about the ${where()} screen · ⌘⏎ to send`,
      }),
      el("button", {
        class: "primary",
        type: "button",
        text: sending ? "Saving…" : "Send",
        disabled: sending ? "disabled" : null,
        onclick: () => send(field),
      }),
    ]),
  ]);
}

async function send(field) {
  const text = field.value.trim();
  if (!text) {
    field.focus();
    return;
  }
  sending = true;
  render();
  try {
    const saved = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ text, where: where() }),
    });
    sending = false;
    open = false;
    render();
    // Naming the file is the point: it says the note is on disk, in the
    // checkout, and will travel with the next push -- rather than "Thanks!",
    // which says nothing about where it went.
    toast(`Saved as ${saved.saved}. It travels with your next update.`, { tone: "good" });
  } catch (error) {
    sending = false;
    render();
    toast(`Not saved — ${error.message}. Copy it somewhere before you lose it.`, { tone: "bad" });
  }
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && open) {
    open = false;
    render();
  }
});

render();
