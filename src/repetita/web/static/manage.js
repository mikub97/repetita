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
let composing = false;
let inbox = [];
//: The order the columns are shown in, which is a property of this screen and
//: not of the course. Never sent to the server: `units.ord` decides what the
//: exported course looks like, and rearranging a board to get two sets next to
//: each other is not a statement about the material.
let order = [];

const ORDER_KEY = "repetita-manage-order";

tab.addEventListener("click", () => show("manage"));

function savedOrder() {
  try {
    const raw = JSON.parse(localStorage.getItem(ORDER_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((id) => typeof id === "string") : [];
  } catch {
    // Private mode, cleared storage, or something else's key. A layout is not
    // worth an exception.
    return [];
  }
}

// Reconcile a remembered layout with the sets that actually exist: ones made
// since it was saved go to the end, ones that have gone drop out. What is
// remembered is an arrangement, and it must never decide which sets there are.
function reconcileOrder() {
  const live = units.map((u) => u.id);
  const kept = savedOrder().filter((id) => live.includes(id));
  order = [...kept, ...live.filter((id) => !kept.includes(id))];
}

function rememberOrder() {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(order));
  } catch {
    /* nothing to do but carry on: the board still works, it just forgets */
  }
}

function ordered() {
  const at = new Map(order.map((id, i) => [id, i]));
  return [...units].sort((a, b) => (at.get(a.id) ?? 0) - (at.get(b.id) ?? 0));
}

// Whether the board is arranged differently from the course itself, which is the
// only time an offer to undo the arrangement means anything.
function rearranged() {
  return units.some((u, i) => order[i] !== u.id);
}

function moveUnit(from, before) {
  const next = order.filter((id) => id !== from);
  const at = next.indexOf(before);
  next.splice(at === -1 ? next.length : at, 0, from);
  order = next;
  rememberOrder();
  render();
}

document.addEventListener("repetita:view", (e) => {
  if (e.detail?.view === "manage") load();
});

async function load() {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    const [material, staged_, queued] = await Promise.all([
      api("/api/material"),
      api("/api/material/pending"),
      api("/api/drafts"),
    ]);
    ({ units, notes } = material);
    reconcileOrder();
    shapes = material.notetypes;
    pending = staged_.changes;
    inbox = queued.drafts;
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
  const hay = `${note.label} ${note.question} ${note.answer} ${note.id} ${note.tags.join(" ")}`;
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

// The bit of the id that is unique within a set -- every id is `<unit>.<rest>`,
// and `rest` collides with nothing beside it (measured: 0 collisions in 30 sets).
function tell(note) {
  const dot_ = note.id.indexOf(".");
  return dot_ === -1 ? note.id : note.id.slice(dot_ + 1);
}

// What to print beside a name that repeats in this column.
//
// The whole suffix is unique, but it is mostly shared prefix --
// `tempo-adverbios-03` against `tempo-adverbios-05` -- and in a 16rem column
// that truncates to `tempo-adverbi…`, throwing away the two characters that
// differ. So use the last segment where that alone separates the rows, which is
// how these ids are actually written: `03`, `07`, `cinema`.
function tells(rows) {
  const byName = new Map();
  for (const n of rows) {
    if (!byName.has(n.label)) byName.set(n.label, []);
    byName.get(n.label).push(n);
  }
  const out = new Map();
  for (const group of byName.values()) {
    if (group.length < 2) continue;
    const full = group.map(tell);
    const last = full.map((t) => t.slice(t.lastIndexOf("-") + 1));
    const enough = new Set(last).size === group.length;
    group.forEach((n, i) => out.set(n.id, enough ? last[i] : full[i]));
  }
  return out;
}

function noteRow(note, { inFamily = false, apart = null } = {}) {
  const isStaged = pending.some((c) => c.note_id === note.id);
  const name = inFamily ? note.variant || note.label : note.label;
  return el(
    "li",
    {
      class: `mnote${isStaged ? " staged" : ""}${note.leaks.length ? " leaking" : ""}` +
        `${selected.has(note.id) ? " picked" : ""}${inFamily ? " in-family" : ""}`,
      draggable: "true",
      ondragstart: (e) => {
        e.stopPropagation();
        dragging = { kind: "notes", ids: selectionOf(note) };
      },
      onclick: (e) => {
        if (e.metaKey || e.ctrlKey) {
          selected.has(note.id) ? selected.delete(note.id) : selected.add(note.id);
          render();
        } else {
          openEditor(note.id);
        }
      },
      // The row is a name; the sentence behind it is one hover away. A column
      // of full questions read well and fitted eight exercises on a screen.
      title: note.leaks.length
        ? note.leaks.join("\n")
        : `${note.question}\n→ ${note.answer}\n${note.id}`,
    },
    [
      dot(note.state, note.state),
      el("span", { class: "mnote-label", text: name }),
      // Only where the name actually repeats in this column. A name is a name,
      // not an identifier: fifteen exercises in one set legitimately answer "o",
      // and the fix is to say which, not to invent text nobody wrote.
      apart && apart.has(note.id)
        ? el("span", { class: "mnote-tell muted", text: apart.get(note.id) })
        : null,
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
      ondragstart: (e) => {
        e.stopPropagation();
        dragging = { kind: "notes", ids: members.map((m) => m.id) };
      },
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
  const apart = tells(loose);
  // Staged for removal. Without this the × does nothing visible to the column
  // it was clicked on, and the only sign is a line in the drawer.
  const going = pending.some((c) => c.kind === "remove_set" && c.note_id === unit.id);
  const rows = [
    ...[...families].map(([word, members]) => familyRow(word, members)),
    ...loose.map((n) => noteRow(n, { apart })),
  ];
  return el(
    "section",
    {
      class: `munit${going ? " going" : ""}`,
      "data-unit": unit.id,
      ondragover: (e) => {
        e.preventDefault();
        if (dragging?.kind === "unit" && dragging.id !== unit.id) {
          e.currentTarget.classList.add("landing");
        }
      },
      ondragleave: (e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) {
          e.currentTarget.classList.remove("landing");
        }
      },
      ondrop: async (e) => {
        e.preventDefault();
        e.currentTarget.classList.remove("landing");
        const moving = dragging;
        dragging = null;
        if (!moving) return;
        // A set dropped on a set is an arrangement, not an edit: it changes
        // where the column sits and nothing else. Exercises dropped on a set
        // are a move, staged like every other change.
        if (moving.kind === "unit") {
          if (moving.id !== unit.id) moveUnit(moving.id, unit.id);
          return;
        }
        const target = unit.id;
        const movers = notes.filter((n) => moving.ids.includes(n.id) && n.unit !== target);
        if (!movers.length) return render();
        for (const note of movers) {
          const ok = await staged(note.id, "unit", target, () => (note.unit = target));
          if (!ok) break;
        }
        selected.clear();
      },
    },
    [
      el(
        "h3",
        {
          class: "munit-title",
          // The whole column moves by its header. Thirty sets no longer fit on
          // one line, so two you want to drag between can be a screen apart --
          // this is how you put them side by side first.
          draggable: "true",
          ondragstart: () => (dragging = { kind: "unit", id: unit.id }),
        },
        [
        el("span", { class: "munit-name", text: title, title: unit.id }),
        el("span", {
          class: "munit-count muted",
          text: going ? "removing" : String(shown.length),
        }),
        el("button", {
          class: "munit-remove",
          type: "button",
          text: going ? "\u21a9" : "\u00d7",
          title: going
            ? "Keep this set after all"
            : "Remove this set \u2014 staged like everything else, and archived rather than deleted",
          onclick: async (e) => {
            e.stopPropagation();
            try {
              if (going) {
                await api("/api/material/discard", {
                  method: "POST",
                  body: JSON.stringify({ note_id: unit.id, kind: "remove_set" }),
                });
              } else {
                await api(`/api/sets/${encodeURIComponent(unit.id)}/remove`, { method: "POST" });
              }
            } catch (error) {
              document.getElementById("status").textContent = `could not: ${error.message}`;
              return;
            }
            pending = (await api("/api/material/pending")).changes;
            render();
          },
        }),
        ],
      ),
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

  const nameInput = el("input", { class: "mfield-input", value: note.label || "" });
  nameInput.value = note.label || "";
  nameInput.addEventListener("change", async () => {
    const next = nameInput.value.trim();
    if (!next) {
      nameInput.value = note.label || "";
      return;
    }
    await staged(note.id, "label", next, () => (note.label = next));
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
      el("label", { class: "mfield-label" }, [
        el("span", { text: "name" }),
        // Derived from the answer until somebody disagrees with it; typing here
        // pins it, and a later edit to the answer leaves it alone.
        el("span", { class: "mfield-when muted", text: "what the board and the plans call this" }),
      ]),
      nameInput,
    ]),
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
    el("ul", { class: "mdiff" }, pending.map(diffRow)),
    el("div", { class: "row" }, [
      el("button", { class: "primary", type: "button", text: "Confirm", onclick: confirm_ }),
      el("button", { class: "quiet", type: "button", text: "Discard", onclick: discard }),
    ]),
  ]);
}

// Named, not identified. `gram-atras-passado-ainda.03` told you nothing about
// what was about to change; `atrás` tells you which exercise moved, and the id
// stays one hover away for when that is the thing you need.
function diffRow(c) {
  if (c.kind === "remove_set") {
    const n = c.before;
    return el("li", { class: "mdiff-set" }, [
      el("span", { class: "mdiff-note", text: c.note_id }),
      el("span", { class: "mdiff-kind", text: "remove set" }),
      el("span", {
        class: "mdiff-before",
        text: n
          ? `${n} exercise${n === 1 ? "" : "s"} archived with it`
          : "empty — nothing goes with it",
      }),
    ]);
  }
  return el("li", {}, [
    el("span", { class: "mdiff-note", text: c.label || c.note_id, title: c.note_id }),
    el("span", { class: "mdiff-kind", text: c.kind }),
    el("span", { class: "mdiff-before", text: summarise(c.before) }),
    el("span", { class: "mdiff-arrow", text: "→" }),
    el("span", { class: "mdiff-after", text: summarise(c.after) }),
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
  const bits = [];
  if (report.sets) bits.push(`${report.sets} set${report.sets === 1 ? "" : "s"} removed`);
  if (report.notes || !bits.length) {
    bits.push(`${report.notes} exercise${report.notes === 1 ? "" : "s"} updated`);
  }
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
  const waiting = inbox.filter((d) => !d.processed_at).length;
  return el("div", { class: "mtoolbar" }, [
    search,
    el("span", {
      class: "muted mcount",
      text: query ? `${shown} of ${notes.length}` : `${notes.length} exercises in ${units.length} sets`,
    }),
    newSet,
    // Only while the board is actually arranged differently from the course.
    // An always-present "reset" invites you to wonder what it would reset.
    rearranged()
      ? el("button", {
          class: "quiet",
          type: "button",
          text: "Reset layout",
          title: "Put the sets back in the course's own order",
          onclick: () => {
            order = units.map((u) => u.id);
            try {
              localStorage.removeItem(ORDER_KEY);
            } catch {
              /* nothing to forget */
            }
            render();
          },
        })
      : null,
    el("button", {
      class: "quiet",
      type: "button",
      text: waiting ? `Add material · ${waiting} waiting` : "Add material",
      title: "Paste a lesson as you wrote it down. Nothing is parsed now — an agent shapes it into exercises when you ask.",
      onclick: () => {
        composing = true;
        render();
      },
    }),
  ]);
}

// Where a lesson lands before it is exercises.
//
// Deliberately a blank box rather than a form: what you have at this moment is
// half a page of notes in two languages, not a filled-in exercise. Parsing it
// now would mean either rejecting most of what people actually write down, or
// guessing -- and a guess made here is a wrong exercise you have to find later.
// ADR-0009 has the reasoning; this is the end of it you type into.
function composer() {
  if (!composing) return null;
  const box = el("textarea", {
    class: "mcompose-box",
    rows: "10",
    placeholder:
      "lekcja 11.09 — futuro simples\n  vou + infinitivo\n  ex: vou estudar amanhã\n\nAnything goes: notes, a photo's worth of typing, a list of words. Structure helps the agent, but nothing is required.",
  });
  const close = () => {
    composing = false;
    render();
  };
  return el("div", { class: "mcompose" }, [
    el("h3", { text: "Add material" }),
    el("p", {
      class: "muted",
      text: "Kept exactly as you type it and queued. Nothing here is studied, counted or checked until an agent has turned it into exercises and you have confirmed them.",
    }),
    box,
    el("div", { class: "row" }, [
      el("button", {
        class: "primary",
        type: "button",
        text: "Queue it",
        onclick: async () => {
          const body = box.value.trim();
          if (!body) return close();
          try {
            await api("/api/drafts", { method: "POST", body: JSON.stringify({ body }) });
          } catch (error) {
            document.getElementById("status").textContent = `not queued — ${error.message}`;
            return;
          }
          composing = false;
          await load();
          document.getElementById("status").textContent =
            "queued — ask an agent to shape it, or run `repetita inbox` yourself";
        },
      }),
      el("button", { class: "quiet", type: "button", text: "Cancel", onclick: close }),
    ]),
    inbox.length
      ? el("ul", { class: "minbox" }, inbox.slice(0, 8).map((d) =>
          el("li", { class: d.processed_at ? "done" : "waiting" }, [
            el("span", { class: "minbox-when", text: (d.created_at || "").slice(0, 10) }),
            el("span", { class: "minbox-what", text: d.summary }),
            el("span", {
              class: "minbox-state muted",
              text: d.processed_at ? d.outcome || "done" : "waiting to be shaped",
            }),
          ]),
        ))
      : null,
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

// Where the reader was looking.
//
// Everything here redraws by replacing nodes, and a replaced scroll container
// starts again at the top. Expanding a word six sets along threw the board back
// to the first column and the column back to its first row -- which reads as the
// page moving on its own, and was reported as exactly that.
function scrollNow() {
  const columns = new Map();
  for (const column of panel.querySelectorAll(".munit")) {
    const list = column.querySelector(".mnotes");
    if (list && list.scrollTop) columns.set(column.dataset.unit, list.scrollTop);
  }
  return { page: window.scrollY, columns };
}

function scrollBack(at) {
  for (const column of panel.querySelectorAll(".munit")) {
    const top = at.columns.get(column.dataset.unit);
    const list = column.querySelector(".mnotes");
    if (list && top) list.scrollTop = top;
  }
  // After the columns: restoring their heights can change the page's, and the
  // window scroll has to be the last word.
  if (at.page) window.scrollTo({ top: at.page });
}

function columns() {
  // Grouped once rather than filtered per column: the old board ran
  // `notes.filter` inside a 27-iteration map, which is 20,000 comparisons for
  // every keystroke.
  const byUnit = new Map(units.map((u) => [u.id, []]));
  for (const note of notes) {
    if (byUnit.has(note.unit)) byUnit.get(note.unit).push(note);
  }
  return ordered().map((u) => unitColumn(u, byUnit.get(u.id) || []));
}

// The board is redrawn on its own so that typing in the search box does not
// rebuild the toolbar under the cursor and lose focus mid-word.
function renderBoard() {
  const board = document.getElementById("mboard");
  if (!board) return render();
  const at = scrollNow();
  fill(board, columns());
  scrollBack(at);
}

function render() {
  const at = scrollNow();
  fill(
    panel,
    toolbar(),
    composer(),
    selectionBar(),
    drawer(),
    el("div", { class: "mlayout" }, [
      el("div", { id: "mboard", class: "mboard" }, columns()),
      editor(),
    ]),
  );
  scrollBack(at);
}
