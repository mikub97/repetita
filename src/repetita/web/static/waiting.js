// What you told the app, and can now find again.
//
// Four things were being recorded and none of them could be seen: changes staged
// on the Manage tab, lesson notes queued inside the composer that makes them,
// exercises flagged while studying, and problems filed from the Design tab --
// the last two had readers on the server and no screen anywhere called them.
//
// Something you told the app and cannot find again is worse than something you
// could not tell it: you stop trusting that saying anything does something.

import { api } from "./api.js";
import { el, fill } from "./dom.js";
import { show } from "./designer.js";

const panel = document.getElementById("waiting");
const link = document.getElementById("waiting-link");

let state = null;

link.addEventListener("click", () => show("waiting"));

document.addEventListener("repetita:view", (e) => {
  if (e.detail?.view === "waiting") load();
});

// The count is worth having on every tab, which is the point: staged changes
// used to be invisible the moment you left Manage, so five of them could sit
// there for a week.
export async function refreshCount() {
  try {
    state = await api("/api/waiting");
  } catch {
    return;
  }
  link.textContent = state.total ? `Waiting · ${state.total}` : "";
  link.hidden = !state.total;
}

document.addEventListener("repetita:changed", refreshCount);

async function load() {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    state = await api("/api/waiting");
  } catch (error) {
    fill(panel, el("p", { class: "muted", text: `could not load (${error.message})` }));
    return;
  }
  link.textContent = state.total ? `Waiting · ${state.total}` : "";
  link.hidden = !state.total;
  render();
}

function group(title, said, rows, empty) {
  return el("section", { class: "wgroup" }, [
    el("h3", { text: `${title}${rows.length ? ` · ${rows.length}` : ""}` }),
    el("p", { class: "muted", text: rows.length ? said : empty }),
    rows.length ? el("ul", { class: "wlist" }, rows) : null,
  ]);
}

function row(what, aside, action) {
  return el("li", {}, [
    el("span", { class: "wwhat", text: what }),
    aside ? el("span", { class: "waside muted", text: aside }) : null,
    action || null,
  ]);
}

function go(label, view, detail) {
  return el("button", {
    class: "quiet",
    type: "button",
    text: label,
    onclick: () => show(view, detail),
  });
}

function render() {
  fill(
    panel,
    el("h2", { text: "Waiting" }),
    group(
      "Changes not yet applied",
      "Staged on the Manage tab. They are on the server, so nothing is lost — but nothing happens to your course until you press Confirm.",
      state.changes.map((c) =>
        row(c.what, c.kind.replace("_", " "), go("Go to Confirm", "manage")),
      ),
      "Nothing staged.",
    ),
    group(
      "Lesson notes, not yet exercises",
      "Kept exactly as you typed them. An agent turns them into exercises when you ask — say so in the terminal, or run `repetita inbox`.",
      state.drafts.map((d) => row(d.summary, (d.created_at || "").slice(0, 10))),
      "Nothing queued.",
    ),
    group(
      "Exercises you flagged while studying",
      "Out of the queue until they are fixed. Open one in Create to fix it.",
      state.reports.map((r) =>
        row(
          r.what,
          [r.reason.replace(/_/g, " "), r.note].filter(Boolean).join(" — "),
          go("Fix it", "create", { unit: r.unit }),
        ),
      ),
      "Nothing flagged.",
    ),
    group(
      "Problems you filed about how things are organised",
      "Raised from the Design tab. An agent can act on these — they are what `repetita issues` lists.",
      state.issues.map((i) => row(i.body, i.about || "")),
      "Nothing filed.",
    ),
  );
}

refreshCount();
