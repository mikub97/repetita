// Managing the material.
//
// Units as columns, notes as cards you can drag between them, an editor for one
// exercise at a time, and a Confirm that applies everything at once. Nothing is
// written until you press it: edits are staged server-side, so a refresh, a
// second tab or a closed laptop does not lose them, and Confirm can show what
// will actually change rather than a number.
//
// This is the one screen that sees answers. It has to -- you cannot fix a typo
// in an answer you cannot see -- and it is the deliberate carve-out from
// ADR-0005 described in ADR-0008. What has not changed is the rule that
// matters: while a question is open, the answer is not in the page. That is the
// study path's business, and nothing here touches it.

import { api } from "./api.js";
import { el, clear, fill, dot } from "./dom.js";
import { show } from "./designer.js";

const panel = document.getElementById("manager");
const tab = document.getElementById("tab-manage");

let units = [];
let notes = [];
let shapes = {};
let pending = [];
let editing = null;
let dragging = null;
let selected = new Set();
let query = "";
let expanded = new Set();

tab.addEventListener("click", () => show("manage"));

document.addEventListener("repetita:view", (e) => {
  if (e.detail?.view === "manage") load();
});

async function load() {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    const [material, drafts] = await Promise.all([
      api("/api/material"),
      api("/api/material/pending"),
    ]);
    ({ units, notes } = material);
    shapes = material.notetypes;
    pending = drafts.changes;
    render();
  } catch (error) {
    fill(panel, el("p", { class: "muted", text: `could not load (${error.message})` }));
  }
}

async function stage(noteId, kind, payload) {
  await api("/api/material/stage", {
    method: "POST",
    body: JSON.stringify({ note_id: noteId, kind, payload }),
  });
  pending = (await api("/api/material/pending")).changes;
}

// Stage first, change the page second.
//
// The other order looks identical when it works and loses the edit when it does
// not: the new value sits on screen, nothing is staged, and the next load
// silently replaces it with the old one. This module's whole promise is that
// nothing is written until Confirm *and nothing is lost before it*, so a failed
// stage has to leave the screen showing what the server actually has.
async function staged(noteId, kind, payload, applyLocally) {
  try {
    await stage(noteId, kind, payload);
  } catch (error) {
    document.getElementById("status").textContent =
      `not saved — ${error.message}. Nothing was changed.`;
    await load();
    return false;
  }
  applyLocally();
  render();
  return true;
}

// --- the board ------------------------------------------------------------

// What the search box is looking at. Id included deliberately: it is the one
// handle that never changes, so it is what you fall back to when you know
// exactly which exercise you mean.
function matches(note) {
  if (!query) return true;
  const hay = `${note.label} ${note.answer} ${note.id} ${note.tags.join(" ")}`;
  return hay.toLowerCase().includes(query.toLowerCase());
}

// One word in several forms, collapsed to a row.
//
// The key comes from the cue the course already authors -- "morar — imperfeito,
// eu" -- not from the note id. The id looks like it would work and does not: on
// the real course it produced 404 groups for 676 notes, and its biggest
// "families" were a topic plus a sequence number rather than a word.
function group(mine) {
  const families = new Map();
  const loose = [];
  for (const note of mine) {
    if (!note.family) {
      loose.push(note);
      continue;
    }
    if (!families.has(note.family)) families.set(note.family, []);
    families.get(note.family).push(note);
  }
  // A family of one is not a family. Rendering chrome round a single exercise
  // claims a relationship that is not there.
  for (const [key, members] of [...families]) {
    if (members.length === 1) {
      loose.push(members[0]);
      families.delete(key);
    }
  }
  return { families, loose };
}

function selectionOf(note) {
  return selected.has(note.id) ? [...selected] : [note.id];
}

function noteRow(note, { inFamily = false } = {}) {
  const isStaged = pending.some((c) => c.note_id === note.id);
  return el(
    "li",
    {
      class: `mnote${isStaged ? " staged" : ""}${note.leaks.length ? " leaking" : ""}` +
        `${selected.has(note.id) ? " picked" : ""}${inFamily ? " in-family" : ""}`,
      draggable: "true",
      ondragstart: () => (dragging = selectionOf(note)),
      onclick: (e) => {
        if (e.metaKey || e.ctrlKey) {
          selected.has(note.id) ? selected.delete(note.id) : selected.add(note.id);
          render();
        } else {
          openEditor(note.id);
        }
      },
      title: note.leaks.length ? note.leaks.join("\n") : note.id,
    },
    [
      dot(note.state, note.state),
      el("span", { class: "mnote-label", text: inFamily ? note.variant || note.label : note.label }),
      el("span", { class: "mnote-answer", text: note.answer }),
      note.leaks.length ? el("span", { class: "mnote-warn", text: "⚠" }) : null,
    ],
  );
}

function familyRow(word, members) {
  const open = expanded.has(word);
  const header = el(
    "div",
    {
      class: "mfamily-head",
      draggable: "true",
      // Dragging the header moves the whole word; dragging a row inside moves
      // one form. Two intentions, and the drag should not make you guess which
      // one you performed.
      ondragstart: () => (dragging = members.map((m) => m.id)),
      onclick: () => {
        open ? expanded.delete(word) : expanded.add(word);
        render();
      },
    },
    [
      el("span", { class: "mfamily-caret", text: open ? "▾" : "▸" }),
      el("span", { class: "mfamily-word", text: word }),
      el("span", { class: "mfamily-count muted", text: String(members.length) }),
    ],
  );
  return el("li", { class: "mfamily" }, [
    header,
    open ? el("ul", { class: "mnotes" }, members.map((m) => noteRow(m, { inFamily: true }))) : null,
  ]);
}

function unitColumn(unit, mine) {
  const shown = mine.filter(matches);
  const { families, loose } = group(shown);
  const title = unit.title?.en || unit.title?.pl || unit.id;
  const rows = [
    ...[...families].map(([word, members]) => familyRow(word, members)),
    ...loose.map((n) => noteRow(n)),
  ];
  return el(
    "section",
    {
      class: "munit",
      ondragover: (e) => e.preventDefault(),
      ondrop: async (e) => {
        e.preventDefault();
        const moving = dragging;
        dragging = null;
        if (!moving) return;
        const target = unit.id;
        const movers = notes.filter((n) => moving.includes(n.id) && n.unit !== target);
        if (!movers.length) return render();
        for (const note of movers) {
          const ok = await staged(note.id, "unit", target, () => (note.unit = target));
          if (!ok) break;
        }
        selected.clear();
      },
    },
    [
      el("h3", { class: "munit-title" }, [
        el("span", { class: "munit-name", text: title }),
        el("span", { class: "munit-count muted", text: String(shown.length) }),
      ]),
      el("ul", { class: "mnotes" }, rows),
    ],
  );
}

// --- the editor -----------------------------------------------------------

function fieldRow(note, name, spec) {
  const value = note.fields[name];
  const isList = spec.type === "text_list";
  const shown = isList ? (value || []).join("\n") : (value ?? "");
  const input = el(isList || String(shown).length > 60 ? "textarea" : "input", {
    class: "mfield-input",
    rows: isList ? "3" : "2",
    value: String(shown),
  });
  input.value = String(shown);
  input.addEventListener("change", async () => {
    const raw = input.value.trim();
    const next = isList
      ? raw.split("\n").map((x) => x.trim()).filter(Boolean)
      : raw;
    await staged(
      note.id,
      "fields",
      { [name]: next.length ? next : null },
      () => (note.fields[name] = next),
    );
  });
  return el("div", { class: "mfield" }, [
    el("label", { class: "mfield-label" }, [
      el("span", { text: name }),
      // The distinction that decides whether a note is servable at all: a field
      // shown while the question is open must not contain the answer.
      el("span", {
        class: `mfield-when ${spec.visibility}`,
        text: spec.visibility === "before" ? "shown with the question" : "shown after answering",
      }),
    ]),
    input,
  ]);
}

function openEditor(noteId) {
  editing = noteId;
  render();
}

function editor() {
  const note = notes.find((n) => n.id === editing);
  if (!note) return null;
  const shape = shapes[note.notetype];
  const tagInput = el("input", { class: "mfield-input", value: note.tags.join(", ") });
  tagInput.value = note.tags.join(", ");
  tagInput.addEventListener("change", async () => {
    const next = tagInput.value.split(",").map((t) => t.trim()).filter(Boolean);
    await staged(note.id, "tags", next, () => (note.tags = next));
  });

  return el("aside", { class: "meditor" }, [
    el("div", { class: "row" }, [
      el("h3", { class: "meditor-id", text: note.id }),
      el("button", { class: "quiet", type: "button", text: "Close", onclick: () => { editing = null; render(); } }),
    ]),
    // Said rather than implied, because rule 1 is the one mistake here that
    // cannot be undone: a renamed id takes a learner's progress with it.
    el("p", { class: "muted", text: `${note.notetype} · the id and the type are fixed — changing either would lose the history stored against this exercise.` }),
    ...note.leaks.map((l) => el("p", { class: "mleak", text: l })),
    ...(note.warnings || []).map((w) => el("p", { class: "mwarn muted", text: w })),
    ...Object.entries(shape?.fields || {}).map(([name, spec]) => fieldRow(note, name, spec)),
    el("div", { class: "mfield" }, [
      el("label", { class: "mfield-label" }, [el("span", { text: "tags" })]),
      tagInput,
    ]),
    el("div", { class: "row" }, [
      el("button", {
        class: "quiet", type: "button", text: "Remove from the course",
        title: "Archived, never deleted — everything you have studied stays",
        onclick: async () => {
          await staged(note.id, "archive", true, () => (editing = null));
        },
      }),
    ]),
  ]);
}

// --- pending, confirm, import ---------------------------------------------

function drawer() {
  if (!pending.length) return null;
  return el("div", { class: "mdrawer" }, [
    el("h3", { text: `${pending.length} change${pending.length === 1 ? "" : "s"} not yet applied` }),
    el("ul", { class: "mdiff" }, pending.map((c) =>
      el("li", {}, [
        el("span", { class: "mdiff-note", text: c.note_id }),
        el("span", { class: "mdiff-kind", text: c.kind }),
        el("span", { class: "mdiff-before", text: summarise(c.before) }),
        el("span", { class: "mdiff-arrow", text: "→" }),
        el("span", { class: "mdiff-after", text: summarise(c.after) }),
      ]),
    )),
    el("div", { class: "row" }, [
      el("button", { class: "primary", type: "button", text: "Confirm", onclick: confirm_ }),
      el("button", { class: "quiet", type: "button", text: "Discard", onclick: discard }),
    ]),
  ]);
}

function summarise(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.join(" · ") || "—";
  if (typeof value === "object") {
    return Object.values(value).map(summarise).join(" · ") || "—";
  }
  return String(value) || "—";
}

async function confirm_() {
  const report = await api("/api/material/confirm", { method: "POST" });
  await load();
  const bits = [`${report.notes} note${report.notes === 1 ? "" : "s"} updated`];
  if (report.cards_added) bits.push(`${report.cards_added} new card(s)`);
  if (report.cards_archived) bits.push(`${report.cards_archived} card(s) retired`);
  if (report.quarantined.length) {
    // Applied, but not servable. Saying so is the whole point: a note that
    // silently stops appearing is worse than one that visibly cannot be used.
    bits.push(`⚠ ${report.quarantined.join(", ")} gives away its own answer and will not be served until fixed`);
  }
  document.getElementById("status").textContent = bits.join(" · ");
}

async function discard() {
  await api("/api/material/discard", { method: "POST", body: JSON.stringify({}) });
  await load();
}

async function importFiles() {
  const preview = await api("/api/import/preview", { method: "POST" });
  if (!preview.conflicts.length) {
    const report = await api("/api/import/apply", { method: "POST", body: JSON.stringify({}) });
    document.getElementById("status").textContent =
      `imported: ${report.added} added, ${report.updated} updated, ${report.archived} archived`;
    return load();
  }
  renderConflicts(preview);
}

function renderConflicts(preview) {
  const chosen = new Set();
  const rows = preview.conflicts.map((c) =>
    el("li", { class: "mclash" }, [
      el("span", { class: "mclash-note", text: c.note_id }),
      el("span", { class: "mclash-side", text: `file: ${summarise(c.file)}` }),
      el("span", { class: "mclash-side", text: `here: ${summarise(c.mine)}` }),
      el("button", {
        class: "quiet", type: "button", text: "take the file",
        onclick: (e) => {
          chosen.add(c.note_id);
          e.target.textContent = "file ✓";
          e.target.classList.add("on");
        },
      }),
    ]),
  );
  fill(panel, 
    el("div", { class: "mconflicts" }, [
      el("h2", { text: "These were changed in both places" }),
      el("p", { class: "muted", text: `${preview.conflicts.length} exercise(s) differ between the course files and your edits here. Anything you do not choose keeps the version you have — importing will never overwrite your work by default.` }),
      el("ul", { class: "mclashes" }, rows),
      el("div", { class: "row" }, [
        el("button", {
          class: "primary", type: "button", text: "Import",
          onclick: async () => {
            const report = await api("/api/import/apply", {
              method: "POST",
              body: JSON.stringify({ take_file: [...chosen] }),
            });
            document.getElementById("status").textContent =
              `imported: ${report.added} added, ${report.updated} updated, ${report.kept_mine.length} kept as yours`;
            load();
          },
        }),
        el("button", {
          class: "quiet", type: "button", text: "Take the file for all",
          onclick: () => {
            preview.conflicts.forEach((c) => chosen.add(c.note_id));
            renderConflicts(preview);
          },
        }),
        el("button", { class: "quiet", type: "button", text: "Cancel", onclick: load }),
      ]),
    ]),
  );
}

function toolbar() {
  const search = el("input", {
    class: "msearch",
    type: "search",
    placeholder: "Search exercises, answers, tags…",
    value: query,
  });
  search.value = query;
  search.addEventListener("input", () => {
    query = search.value;
    renderBoard();
  });

  const newSet = el("button", {
    class: "quiet", type: "button", text: "+ New set",
    onclick: async () => {
      const name = window.prompt("Name for the new set");
      if (!name) return;
      try {
        await api("/api/sets", { method: "POST", body: JSON.stringify({ id: name }) });
      } catch (error) {
        document.getElementById("status").textContent = `could not: ${error.message}`;
        return;
      }
      await load();
    },
  });

  const shown = notes.filter(matches).length;
  return el("div", { class: "mtoolbar" }, [
    search,
    el("span", {
      class: "muted mcount",
      text: query ? `${shown} of ${notes.length}` : `${notes.length} exercises in ${units.length} sets`,
    }),
    newSet,
    el("button", {
      class: "quiet", type: "button", text: "Import from the course files",
      title: "Re-read courses/ and show anything that clashes with your edits",
      onclick: importFiles,
    }),
  ]);
}

function selectionBar() {
  if (!selected.size) return null;
  return el("div", { class: "mselection" }, [
    el("span", { text: `${selected.size} selected` }),
    el("button", {
      class: "quiet", type: "button", text: "Clear",
      onclick: () => {
        selected.clear();
        render();
      },
    }),
    el("button", {
      class: "quiet", type: "button", text: "Archive",
      title: "Archived, never deleted — everything you have studied stays",
      onclick: async () => {
        for (const id of [...selected]) await staged(id, "archive", true, () => {});
        selected.clear();
        render();
      },
    }),
    el("span", { class: "muted", text: "…or drag them onto a set" }),
  ]);
}

// The board is redrawn on its own so that typing in the search box does not
// rebuild the toolbar under the cursor and lose focus mid-word.
function renderBoard() {
  const board = document.getElementById("mboard");
  if (!board) return render();
  const byUnit = new Map(units.map((u) => [u.id, []]));
  for (const note of notes) {
    if (byUnit.has(note.unit)) byUnit.get(note.unit).push(note);
  }
  fill(board, units.map((u) => unitColumn(u, byUnit.get(u.id) || [])));
}

function render() {
  // Grouped once rather than filtered per column: the old board ran
  // `notes.filter` inside a 27-iteration map, which is 20,000 comparisons for
  // every keystroke.
  const byUnit = new Map(units.map((u) => [u.id, []]));
  for (const note of notes) {
    if (byUnit.has(note.unit)) byUnit.get(note.unit).push(note);
  }

  fill(
    panel,
    toolbar(),
    selectionBar(),
    drawer(),
    el("div", { class: "mlayout" }, [
      el("div", { id: "mboard", class: "mboard" },
        units.map((u) => unitColumn(u, byUnit.get(u.id) || []))),
      editor(),
    ]),
  );
}
