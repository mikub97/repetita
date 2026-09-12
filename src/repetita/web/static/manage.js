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
import { el, fill, dot, toast } from "./dom.js";
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
let axes = [];
//: How far along a card is, least advanced first -- `core/buckets.ORDER`, which
//: is what the server sorts by when it picks a note's worst card. Repeated here
//: rather than fetched: it is four words and it has not changed since ADR-0002.
const STATES = ["new", "learning", "young", "mature", "suspended", "retired"];
//: Which axis the columns are banded under, or "" for one flat board.
//: Banding rather than regrouping: a column is a set, and dropping an exercise
//: on it moves the exercise there. If the columns were tracks instead, a drop
//: would mean editing a tag, which is a different act that happens to look the
//: same (ADR-0013 -- a set is a shelf, a tag is a subject).
let banding = "";
//: axis -> Set of values kept. Empty means everything.
let filters = new Map();
//: The set whose name and description are being typed, `{id, title, description}`.
//: One at a time: two open editors is two drafts of the same field with no way
//: to say which wins.
let naming = null;
//: Archived material, fetched only when asked for. `{units, notes}` or null.
let attic = null;
let showingAttic = false;
//: Sets whose column is showing everything rather than the first `CAP`.
let opened = new Set();

//: How many rows a column shows before it says how many it is not showing.
//: The board used to cap by pixels -- `max-height` plus `overflow-y: auto` --
//: which hid 63 of a 76-row set behind a scrollbar that only appeared on hover.
//: A cap you can read beats a cap you have to discover (§4.8).
const CAP = 12;

//: How many values an axis offers before it says how many it is not offering.
//: `topic` has 19 on this course and filled a phone screen on its own -- the
//: same problem as a column showing 13 of 76, so the same answer.
const CHIP_CAP = 8;
//: Axes showing all of their values.
let wideOpen = new Set();
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

function moveUnit(from, target, after) {
  const next = order.filter((id) => id !== from);
  const at = next.indexOf(target);
  // `after` is what makes the last position reachable at all: while this only
  // ever inserted *before* the column you dropped on, nothing could be moved to
  // the end of the board.
  next.splice(at === -1 ? next.length : at + (after ? 1 : 0), 0, from);
  order = next;
  rememberOrder();
  render();
}

// --- saying where a drop will land ----------------------------------------
//
// The board used to answer "where will this go?" with a border around the
// column under the pointer, which says *which* column and not *where* -- and
// the answer was always "before this one", which is not what an outlined box
// means to anybody. Now a set shows a caret in the gap it will drop into, and
// an exercise marks the set that will receive it. Two gestures, two pictures:
// dropping a set *between* sets and dropping an exercise *into* one are
// different acts and must not look alike.

//: Which half of the column the pointer is in -- the side the set will land on.
function side(e) {
  const box = e.currentTarget.getBoundingClientRect();
  return e.clientX > box.left + box.width / 2;
}

function mark(column, what, after) {
  if (!what) return;
  clearMarks();
  if (what.kind === "unit") {
    if (what.id === column.dataset.unit) return;
    column.classList.add(after ? "landing-after" : "landing-before");
  } else if (what.ids?.length) {
    column.classList.add("receiving");
  }
}

function unmark(column) {
  column.classList.remove("landing-before", "landing-after", "receiving");
}

function clearMarks() {
  for (const column of panel.querySelectorAll(".munit")) unmark(column);
}

// Everything a drag left behind, including what it picked up. Separate from
// `clearMarks` because that one runs on every `dragover`, and clearing `lifted`
// there would un-lift the column you are still holding.
function dragFinished() {
  dragging = null;
  clearMarks();
  for (const column of panel.querySelectorAll(".lifted")) column.classList.remove("lifted");
}

document.addEventListener("repetita:view", async (e) => {
  if (e.detail?.view !== "manage") return;
  await load();
  // Create sends you here to name a set, so arriving should open the thing you
  // came for rather than leaving you to find the column yourself.
  if (e.detail.name) {
    const unit = units.find((u) => u.id === e.detail.name);
    if (unit) {
      openNaming(unit);
      panel.querySelector(`[data-unit="${CSS.escape(unit.id)}"]`)
        ?.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }
});

// What a set is staged to be called, if anything. The column header reads this
// rather than the unit row, so a staged name is on screen before Confirm --
// otherwise naming a set looks like it did nothing until you press it.
function stagedName(unitId) {
  return pending.find((c) => c.kind === "set_name" && c.note_id === unitId)?.payload || null;
}

// The name to print, in the order of what someone actually decided: what is
// staged, then what is saved, then nothing. `null` means nameless, and the
// column says so rather than printing the id as though it were a name.
function nameOf(unit) {
  const want = stagedName(unit.id);
  const title = (want && want.title) || unit.title || {};
  return title.en || title.pl || null;
}

function describedBy(unit) {
  const want = stagedName(unit.id);
  const description = (want && want.description) || unit.description || {};
  return description.en || description.pl || "";
}

function openNaming(unit) {
  const want = stagedName(unit.id);
  naming = {
    id: unit.id,
    title: { ...((want && want.title) || unit.title || {}) },
    description: { ...((want && want.description) || unit.description || {}) },
  };
  render();
}

async function load() {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    const [material, staged_, queued] = await Promise.all([
      api("/api/material"),
      api("/api/material/pending"),
      api("/api/drafts"),
    ]);
    ({ units, notes } = material);
    axes = material.axes || [];
    // Refetched rather than kept: a Confirm that restored something has just
    // made the cached copy wrong, and a stale attic offers to restore material
    // that is already back.
    attic = showingAttic ? await api("/api/material/archived") : null;
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
  document.dispatchEvent(new CustomEvent("repetita:changed"));
}

// Stage first, change the page second.
//
// The other order looks identical when it works and loses the edit when it does
// not: the new value sits on screen, nothing is staged, and the next load
// silently replaces it with the old one. This module's whole promise is that
// nothing is written until Confirm *and nothing is lost before it*, so a failed
// stage has to leave the screen showing what the server actually has.
async function staged(noteId, kind, payload, applyLocally) {
  const had = pending.length;
  try {
    await stage(noteId, kind, payload);
  } catch (error) {
    toast(`Not saved — ${error.message}. Nothing was changed.`, { tone: "bad" });
    await load();
    return false;
  }
  applyLocally();
  // A drawer that is already on screen is updated where it stands. Rebuilding
  // the panel would take the Confirm button with it, and a field stages on
  // blur -- which is the same moment you are pressing Confirm.
  if (had && pending.length && refreshDrawer()) renderBoard();
  else render();
  return true;
}

// --- the board ------------------------------------------------------------

// What the search box is looking at. Id included deliberately: it is the one
// handle that never changes, so it is what you fall back to when you know
// exactly which exercise you mean.
function matches(note) {
  for (const [axis, kept] of filters) {
    if (!kept.size) continue;
    const mine = valuesOn(note, axis);
    // A note filed under nothing on this axis is not a match for a value on it.
    // `topic` covers 626 of 757, so the other 131 should disappear when you ask
    // for a topic rather than quietly pass.
    if (!mine.some((v) => kept.has(v))) return false;
  }
  if (!query) return true;
  const hay = `${note.label} ${note.question} ${note.answer} ${note.id} ${note.tags.join(" ")}`;
  return hay.toLowerCase().includes(query.toLowerCase());
}

// What a note is filed under on one axis. Two of these are not facets at all --
// `state` is the scheduler's answer and `hand` is who wrote it -- but they are
// the same question shape, so the board asks them the same way.
function valuesOn(note, axis) {
  if (axis === "state") return [note.state];
  if (axis === "hand") return [handOf(note)];
  if (axis === "lesson") return note.lesson ? [note.lesson] : [];
  return note.facets?.[axis] || [];
}

function filtering() {
  return [...filters.values()].some((v) => v.size);
}

// Which band a set belongs to, by what most of its exercises are.
//
// A set is a shelf and its contents decide what kind of shelf it is; there is
// no separate field saying so, and inventing one would be a fifth concept for a
// question the data already answers (ADR-0013).
function bandOf(unit, mine) {
  if (banding === "prefix") {
    const cut = unit.id.indexOf("-");
    return cut === -1 ? unit.id : unit.id.slice(0, cut);
  }
  const counts = new Map();
  for (const note of mine) {
    for (const value of note.facets?.[banding] || []) {
      counts.set(value, (counts.get(value) || 0) + 1);
    }
  }
  if (!counts.size) return null;
  // Ties go to the axis's own order, which is the order a person declared --
  // alphabetical would put B1 above A1 and call it a shelf.
  const declared = axes.find((a) => a.axis === banding)?.values || [];
  let best = null;
  for (const [value, n] of counts) {
    const better =
      !best ||
      n > best.n ||
      (n === best.n && declared.indexOf(value) > -1 &&
        (declared.indexOf(best.value) === -1 || declared.indexOf(value) < declared.indexOf(best.value)));
    if (better) best = { value, n };
  }
  return best.value;
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

// Who wrote this exercise, from two fields that were already there.
//
// `origin` is the source file and is empty for anything written in the app;
// `edited_at` is non-null once someone has changed it here. Three states, and
// they are the requirement ADR-0013 rule 3 states: "material I wrote",
// "material an agent wrote" and "material I changed after an agent wrote it"
// should be three visibly different things.
//: `mark: null` means the row shows nothing. Material from an untouched file is
//: the overwhelming default -- 757 of 757 today -- and a mark repeated on every
//: row is noise that says nothing. What is worth a glyph is the exception: this
//: one was written here, or somebody has been at it since. The editor pane still
//: says it in words for every exercise, which is where you are when you care.
const HANDS = {
  file: { mark: null, says: "from a course file" },
  here: { mark: "✎", says: "written here" },
  mixed: { mark: "✚", says: "from a file, changed here" },
};

// A stamp as a date somebody would say out loud.
function said(stamp) {
  const when = new Date(stamp);
  return Number.isNaN(when.valueOf())
    ? stamp.slice(0, 10)
    : when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function handOf(note) {
  if (!note.origin) return "here";
  return note.edited_at ? "mixed" : "file";
}

function provenance(note, { always = false } = {}) {
  const hand = handOf(note);
  const mark = HANDS[hand].mark;
  if (!mark && !always) return null;
  const where = note.origin ? ` — ${note.origin}` : "";
  const when = note.edited_at ? ` — edited ${note.edited_at.slice(0, 10)}` : "";
  return el("span", {
    class: `mhand mhand-${hand}`,
    text: mark || "▤",
    title: `${HANDS[hand].says}${where}${when}`,
  });
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

const TELL_MAX = 30;

function cut(text) {
  const tidy = text.replace(/\s+/g, " ").trim();
  return tidy.length > TELL_MAX ? `${tidy.slice(0, TELL_MAX - 1)}…` : tidy;
}

// The cue, cut to something that fits beside a name.
//
// Cues are written as a chain -- `SER — profissão — SER + profissão (sem
// artigo)` -- where the first link or two is the distinction and the rest is the
// explanation. In a 16rem column the whole chain truncates to `SER — profi…`,
// so take the head of it rather than the head of the string.
function gist(note) {
  const cue = note.fields?.cue;
  if (typeof cue !== "string" || !cue.trim()) return null;
  return cut(cue.split(/\s*[—–]\s*/).filter(Boolean).slice(0, 2).join(" — "));
}

// The question itself, for the rows a cue does not separate -- three exercises
// cued `wzmocnienie pytania` are told apart by `O que`, `Onde`, `Quando`, which
// is the first thing in each prompt.
function sketch(note) {
  for (const name of ["prompt", "situation", "source", "l1"]) {
    const value = note.fields?.[name];
    if (typeof value === "string" && value.trim()) return cut(value);
  }
  return null;
}

// What to print beside a name that repeats in this column.
//
// Twenty rows called `o` is not a bug in `label` -- `docs/labels.md` is right
// that names repeat, and for the study loop a name is a name. It is a bug in
// what the board printed to tell them apart: a two-digit tail of the id, which
// identifies nothing to a reader. `ser-estar-01` against `ser-estar-05` says
// less than `SER — profissão` against `SER — evento no tempo`.
//
// So: prefer what a person actually wrote. Candidates in order of how much they
// mean to a human, and the first one that separates every row in the group wins
// (ADR-0013). The id tail stays last because it is the only one guaranteed to
// be unique -- an unhelpful label beats two rows you cannot tell apart.
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
    const candidates = [
      group.map(gist),
      // `genero-cinema`, `genero-problema` -- these ids were written with the
      // distinguishing word at the end, and where that is what they are, it is
      // the best thing on offer: shorter than the prompt and deliberately
      // chosen. Skipped when it is a sequence number, which says nothing that
      // the row's position does not.
      full.map((t) => {
        const last = t.slice(t.lastIndexOf("-") + 1);
        return /^\d+$/.test(last) ? null : last;
      }),
      group.map(sketch),
      full,
    ];
    const enough = candidates.find(
      (c) => c.every(Boolean) && new Set(c).size === group.length,
    );
    if (!enough) continue;
    group.forEach((n, i) => out.set(n.id, enough[i]));
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
      ondragend: dragFinished,
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
      provenance(note),
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
      ondragend: dragFinished,
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
  // A slug is an identifier, not a name (ADR-0013). Where nobody has written
  // one the column says so and offers to take one, rather than printing the id
  // in the style of a title -- which is how 31 nameless sets came to look like
  // 31 named ones.
  const named = nameOf(unit);
  const about = describedBy(unit);
  const apart = tells(loose);
  // Staged for removal. Without this the × does nothing visible to the column
  // it was clicked on, and the only sign is a line in the drawer.
  const going = pending.some((c) => c.kind === "remove_set" && c.note_id === unit.id);
  const all = [
    ...[...families].map(([word, members]) => familyRow(word, members)),
    ...loose.map((n) => noteRow(n, { apart })),
  ];
  const open = opened.has(unit.id);
  const hidden = open ? 0 : Math.max(0, all.length - CAP);
  const rows = hidden ? all.slice(0, CAP) : all;
  if (hidden || open) {
    rows.push(
      el("li", { class: "mnote-more" }, [
        el("button", {
          class: "quiet",
          type: "button",
          text: hidden ? `… and ${hidden} more` : "show fewer",
          onclick: (e) => {
            e.stopPropagation();
            open ? opened.delete(unit.id) : opened.add(unit.id);
            render();
          },
        }),
      ]),
    );
  }
  return el(
    "section",
    {
      class: `munit${going ? " going" : ""}${open ? " open" : ""}`,
      "data-unit": unit.id,
      ondragover: (e) => {
        e.preventDefault();
        mark(e.currentTarget, dragging, side(e));
      },
      ondragleave: (e) => {
        // `dragleave` also fires on the way into a child. Without this the
        // marker flickers off every time the pointer crosses a row.
        if (!e.currentTarget.contains(e.relatedTarget)) unmark(e.currentTarget);
      },
      ondrop: async (e) => {
        e.preventDefault();
        const after = side(e);
        unmark(e.currentTarget);
        const moving = dragging;
        dragging = null;
        if (!moving) return;
        // A set dropped on a set is an arrangement, not an edit: it changes
        // where the column sits and nothing else. Exercises dropped on a set
        // are a move, staged like every other change.
        if (moving.kind === "unit") {
          if (moving.id !== unit.id) moveUnit(moving.id, unit.id, after);
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
          //
          // Not while the board is banded: a band comes from what a set holds,
          // so a column dragged into another one would spring back. Refusing
          // the gesture is better than performing it and undoing it.
          draggable: banding ? null : "true",
          ondragstart: (e) => {
            if (banding) return;
            dragging = { kind: "unit", id: unit.id };
            e.currentTarget.closest(".munit")?.classList.add("lifted");
          },
          // Fires however a drag ends, including one abandoned over nothing --
          // which used to leave the last marker it drew on the board.
          ondragend: dragFinished,
        },
        [
        banding
          ? null
          : el("span", { class: "munit-grip", text: "⠿", title: "Drag to move this set" }),
        named
          ? el("span", {
              class: "munit-name",
              text: named,
              title: about ? `${about}\n\n${unit.id}` : unit.id,
            })
          : el("span", {
              class: "munit-name unnamed",
              text: unit.id,
              title: "This set has no name — click to give it one",
            }),
        el("button", {
          class: "munit-name-edit",
          type: "button",
          text: named ? "✎" : "name it",
          title: named
            ? "Rename this set, or describe what it is for"
            : "Give this set a name a person wrote",
          onclick: (e) => {
            e.stopPropagation();
            openNaming(unit);
          },
        }),
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
              toast(`Could not — ${error.message}`, { tone: "bad" });
              return;
            }
            pending = (await api("/api/material/pending")).changes;
            render();
          },
        }),
        ],
      ),
      naming && naming.id === unit.id ? namingForm(unit) : null,
      // Said once, under the name, rather than hidden in a tooltip: it is the
      // half of "what is this shelf for" that a name has no room for.
      !naming && about ? el("p", { class: "munit-about muted", text: about }) : null,
      el("ul", { class: "mnotes" }, rows),
    ],
  );
}

// Naming a set, staged like every other change on this tab (ADR-0013).
//
// Removing a set already waited for Confirm and renaming one did not, so the
// two operations on a set's existence lived in different tabs under opposite
// commit models. This is the end of that: both are here, both wait.
function namingForm(unit) {
  const name = el("input", {
    class: "munit-input",
    placeholder: "A name for it",
    value: naming.title.en || naming.title.pl || "",
  });
  name.addEventListener("input", () => {
    const text = name.value.trim();
    naming.title = text ? { en: text } : {};
  });

  const about = el("textarea", {
    class: "munit-input munit-about-input",
    rows: 2,
    placeholder: "What it is for — what it assumes, what it drills (optional)",
  });
  // Not an attribute: a textarea's value is its text content, and `el` sets
  // attributes, so `value:` on this one would have rendered an empty box.
  about.value = naming.description.en || naming.description.pl || "";
  about.addEventListener("input", () => {
    const text = about.value.trim();
    naming.description = text ? { en: text } : {};
  });

  const save = async () => {
    const want = naming;
    naming = null;
    try {
      await api(`/api/sets/${encodeURIComponent(want.id)}`, {
        method: "PUT",
        body: JSON.stringify({ title: want.title, description: want.description }),
      });
      pending = (await api("/api/material/pending")).changes;
    } catch (error) {
      toast(`Not named — ${error.message}`, { tone: "bad" });
    }
    render();
  };

  return el("div", { class: "munit-naming" }, [
    name,
    about,
    el("div", { class: "row" }, [
      el("button", { class: "primary", type: "button", text: "Name it", onclick: save }),
      el("button", {
        class: "quiet",
        type: "button",
        text: "Cancel",
        onclick: () => {
          naming = null;
          render();
        },
      }),
      el("span", { class: "muted", text: "· waits for Confirm" }),
    ]),
  ]);
}

// --- the editor -----------------------------------------------------------

// One field, read-only.
//
// This screen files material; it does not write it. Editing the same exercise in
// two places meant two editors, two field orders, and two different promises
// about when a change lands -- so the words live here to be *read*, with the
// distinction that decides whether an exercise is servable at all, and the one
// editor is a click away.
function fieldRow(note, name, spec) {
  const value = note.fields[name];
  const shown = Array.isArray(value) ? value.join(" · ") : (value ?? "");
  if (!String(shown).trim()) return null;
  return el("div", { class: "mfield" }, [
    el("label", { class: "mfield-label" }, [
      el("span", { text: name }),
      el("span", {
        class: `mfield-when ${spec.visibility}`,
        text: spec.visibility === "before" ? "shown with the question" : "shown after answering",
      }),
    ]),
    el("p", { class: "mfield-said", text: String(shown) }),
  ]);
}

//: Where Create looks when nothing told it which set to open. Written here
//: because this is the tab where you are *reading* a set, and localStorage
//: rather than a shared variable so it survives a reload -- arriving at Create
//: on "New set…" after closing the laptop is the same annoyance as arriving
//: there from a set you had open.
const LAST_SET_KEY = "repetita-last-set";

function rememberSet(unitId) {
  try {
    localStorage.setItem(LAST_SET_KEY, unitId);
  } catch {
    /* a browser that refuses storage still works, it just forgets */
  }
}

function openEditor(noteId) {
  editing = noteId;
  const note = notes.find((n) => n.id === noteId);
  if (note) rememberSet(note.unit);
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

  // The set this exercise is in, as a control. Dragging was the only way to
  // move one, which is fine when the two sets are side by side and impossible
  // when they are four rows apart.
  const setPick = el("select", { class: "mfield-input" }, [
    // The name, now that sets have one (ADR-0013). This listed slugs because
    // there was nothing else to list.
    ...ordered().map((u) => el("option", { value: u.id, text: nameOf(u) || u.id })),
  ]);
  setPick.value = note.unit;
  setPick.addEventListener("change", async () => {
    const target = setPick.value;
    if (target === note.unit) return;
    await staged(note.id, "unit", target, () => (note.unit = target));
  });

  // Reading order, not the order a JSON object happened to arrive in: Flask
  // sorts the keys of everything it serialises, so this listed `audio` first
  // and the actual question fourth.
  const order_ = shape?.order || Object.keys(shape?.fields || {});

  return el("aside", { class: "meditor" }, [
    el("div", { class: "row" }, [
      // The name, not the id. `fala-capoeiristas.01` is a directory and a
      // sequence number; the board, the drawer and the plans all call this
      // exercise something, and so should the screen that is about it.
      el("h3", { class: "meditor-id", text: note.label || note.question, title: note.id }),
      el("button", { class: "quiet", type: "button", text: "Close", onclick: () => { editing = null; render(); } }),
    ]),
    el("p", { class: "muted", text: `${note.notetype} · ${note.unit}` }),
    // Where it came from, in words rather than as the mark the row carries.
    // The row has to be scannable; this is the place there is room to say it.
    el("p", { class: "muted meditor-hand" }, [
      provenance(note, { always: true }),
      el("span", {
        text: note.origin
          ? `from ${note.origin}${note.edited_at ? `, edited by you on ${said(note.edited_at)}` : ""}`
          : `written here${note.edited_at ? `, last changed ${said(note.edited_at)}` : ""}`,
      }),
    ]),
    ...note.leaks.map((l) => el("p", { class: "mleak", text: l })),
    ...(note.warnings || []).map((w) => el("p", { class: "mwarn muted", text: w })),
    ...order_.map((name) => fieldRow(note, name, shape.fields[name])),
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
    el("div", { class: "mfield" }, [
      el("label", { class: "mfield-label" }, [
        el("span", { text: "set" }),
        el("span", { class: "mfield-when muted", text: "staged, like a drag" }),
      ]),
      setPick,
    ]),
    el("div", { class: "row" }, [
      el("button", {
        class: "primary",
        type: "button",
        text: "Edit this exercise →",
        title: "Open it in Create, where the words are written — with the preview and the checks",
        onclick: () => show("create", { unit: note.unit, note: note.id }),
      }),
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
    el("h3", { id: "mdrawer-head", text: drawerHead() }),
    el("ul", { id: "mdrawer-rows", class: "mdiff" }, pending.map(diffRow)),
    el("div", { class: "row" }, [
      el("button", { class: "primary", type: "button", text: "Confirm", onclick: confirm_ }),
      el("button", { class: "quiet", type: "button", text: "Discard", onclick: discard }),
    ]),
  ]);
}

function drawerHead() {
  return `${pending.length} change${pending.length === 1 ? "" : "s"} not yet applied`;
}

// Update what the drawer says without replacing the button that applies it.
//
// Staging used to re-render the whole panel, which swaps the Confirm button for
// an identical new one -- and a field edit stages on blur, so pressing Confirm
// straight from a field meant mousedown on one button and mouseup on its
// replacement. No click, nothing happens, and the only thing you can do about
// it is press again.
function refreshDrawer() {
  const head = document.getElementById("mdrawer-head");
  const rows = document.getElementById("mdrawer-rows");
  if (!head || !rows) return false;
  head.textContent = drawerHead();
  fill(rows, pending.map(diffRow));
  return true;
}

// Named, not identified. `gram-atras-passado-ainda.03` told you nothing about
// what was about to change; `atrás` tells you which exercise moved, and the id
// stays one hover away for when that is the thing you need.
// What a staged change is called on screen.
//
// The drawer printed `c.kind` -- the raw key from `store/material.py` -- beside
// rows whose visible text uses the other vocabulary entirely: `unit` next to a
// set's name, `fields` next to an exercise's. One glossary, in the words the
// rest of the tab already uses (§7 of the design review).
const SAID = {
  fields: "wording",
  tags: "tags",
  unit: "moved to set",
  label: "name",
  archive: "removed",
  restore: "restored",
  remove_set: "remove set",
  restore_set: "restore set",
  set_name: "name set",
};

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
  if (c.kind === "restore_set") {
    const n = c.before;
    return el("li", { class: "mdiff-set" }, [
      el("span", { class: "mdiff-note", text: c.note_id }),
      el("span", { class: "mdiff-kind", text: "restore set" }),
      el("span", {
        class: "mdiff-before",
        text: n
          ? `${n} exercise${n === 1 ? "" : "s"} come back with it`
          : "the set itself — nothing was archived with it",
      }),
    ]);
  }
  if (c.kind === "set_name") {
    // Only the parts being changed reach here, so the row says "name" or
    // "description" rather than claiming both were rewritten.
    const parts = Object.keys(c.after || {});
    return el("li", { class: "mdiff-set" }, [
      el("span", { class: "mdiff-note", text: c.note_id }),
      el("span", { class: "mdiff-kind", text: parts.includes("new_id") ? "rename set" : "name set" }),
      el("span", { class: "mdiff-before", text: summarise(c.before) }),
      el("span", { class: "mdiff-arrow", text: "→" }),
      el("span", { class: "mdiff-after", text: summarise(c.after) }),
    ]);
  }
  return el("li", {}, [
    el("span", { class: "mdiff-note", text: c.label || c.note_id, title: c.note_id }),
    el("span", { class: "mdiff-kind", text: SAID[c.kind] || c.kind }),
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
  let report;
  try {
    report = await api("/api/material/confirm", { method: "POST" });
  } catch (error) {
    // There was no `catch` here at all: a confirm that failed said nothing, and
    // the drawer sat there looking exactly as it had.
    toast(`Nothing was applied — ${error.message}`, { tone: "bad" });
    return;
  }
  await load();
  const bits = [];
  if (report.sets) bits.push(`${report.sets} set${report.sets === 1 ? "" : "s"} removed`);
  if (report.restored) {
    bits.push(`${report.restored} set${report.restored === 1 ? "" : "s"} restored`);
  }
  if (report.named) bits.push(`${report.named} set${report.named === 1 ? "" : "s"} named`);
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
  toast(bits.join(" · "), { tone: report.quarantined.length ? "warn" : "good" });
  document.dispatchEvent(new CustomEvent("repetita:changed"));
}

async function discard() {
  const n = pending.length;
  try {
    await api("/api/material/discard", { method: "POST", body: JSON.stringify({}) });
  } catch (error) {
    toast(`Nothing was discarded — ${error.message}`, { tone: "bad" });
    return;
  }
  await load();
  toast(`${n} change${n === 1 ? "" : "s"} discarded`);
  document.dispatchEvent(new CustomEvent("repetita:changed"));
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

  // Handed to Create rather than done here. A set made by a prompt box is an
  // empty set you then have to fill somewhere else; there is one place for
  // writing exercises now, and this is the way to it.
  const newSet = el("button", {
    class: "quiet",
    type: "button",
    text: "+ New set",
    onclick: () => show("create", { unit: "" }),
  });

  // Banding and filtering. The axes come from the course -- `level`, `track`,
  // `source`, `topic` on this one -- and until now the board could reach none of
  // them: `/api/catalogue` has served them since Design was built, and this tab
  // grouped by set and offered a search box (ADR-0013).
  const band = el("select", { class: "mband-pick", title: "Group the sets into shelves" }, [
    el("option", { value: "", text: "no grouping" }),
    ...axes
      .filter((a) => a.axis !== "topic")
      .map((a) => el("option", { value: a.axis, text: `by ${a.title?.en || a.axis}` })),
    el("option", { value: "prefix", text: "by name prefix" }),
  ]);
  band.value = banding;
  band.addEventListener("change", () => {
    banding = band.value;
    render();
  });

  const shown = notes.filter(matches).length;
  // Said permanently. The drawer appearing is the only thing that ever
  // suggested edits here wait, and it is not on screen until you have made one.
  const waiting = inbox.filter((d) => !d.processed_at).length;
  return el("div", { class: "mtoolbar" }, [
    search,
    band,
    el("span", {
      class: "muted mcount",
      text:
        query || filtering()
          ? `${shown} of ${notes.length}`
          : `${notes.length} exercises in ${units.length} sets`,
    }),
    el("span", {
      class: "muted mpromise",
      text: "· changes here wait for Confirm",
      title: "Nothing on this tab reaches your course until you press Confirm",
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
      class: `quiet${showingAttic ? " on" : ""}`,
      type: "button",
      text: "Archived",
      title:
        "Material that has left the course. Archived, never deleted — this is the way back to it.",
      onclick: async () => {
        showingAttic = !showingAttic;
        if (showingAttic && !attic) {
          try {
            attic = await api("/api/material/archived");
          } catch (error) {
            toast(`Could not read the archive — ${error.message}`, { tone: "bad" });
            showingAttic = false;
          }
        }
        render();
      },
    }),
    el("button", {
      class: "quiet",
      type: "button",
      // "Add material" is what a person presses when they want to add material,
      // and this is the one route that does not do that: it queues text for an
      // agent to shape later. The direct way to add an exercise is the Create
      // tab, and the label should not compete with it.
      text: waiting ? `Capture a lesson · ${waiting} waiting` : "Capture a lesson",
      title: "Paste a lesson as you wrote it down. Nothing is parsed now — an agent shapes it into exercises when you ask.",
      onclick: () => {
        composing = true;
        render();
      },
    }),
  ]);
}

// What you can narrow the board to.
//
// Values come from the material rather than from the declared list: `topic`
// declares none at all on this course and is filed on 626 of 757 notes, so a
// control built from the declaration would have been empty and a control built
// from the data is the useful one. Declared order is still honoured where there
// is one, because A1/A2/B1 is a sequence and alphabetical only looks like one.
function filterBar() {
  const rows = [];
  for (const axis of [...axes.map((a) => a.axis), "lesson", "state", "hand"]) {
    const counts = new Map();
    for (const note of notes) {
      for (const v of valuesOn(note, axis)) counts.set(v, (counts.get(v) || 0) + 1);
    }
    if (counts.size < 2) continue; // one value is not a choice
    const declared =
      axis === "state"
        ? STATES
        : axis === "hand"
          ? Object.keys(HANDS)
          : axis === "lesson"
            ? // Newest first: the question this answers is almost always about
              // the lesson you just had, not the one in September.
              [...counts.keys()].sort().reverse()
            : axes.find((a) => a.axis === axis)?.values || [];
    const values = [...counts.keys()].sort((a, b) => {
      const ai = declared.indexOf(a);
      const bi = declared.indexOf(b);
      if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return counts.get(b) - counts.get(a);
    });
    const kept = filters.get(axis) || new Set();
    // Never hide a value that is currently doing something: a filter you cannot
    // see is a filter you cannot turn off.
    const wide = wideOpen.has(axis);
    const shown = wide ? values : values.filter((v, i) => i < CHIP_CAP || kept.has(v));
    const more = values.length - shown.length;
    const title =
      axis === "state"
        ? "State"
        : axis === "hand"
          ? "Written by"
          : axis === "lesson"
            ? "Arrived"
            : axes.find((a) => a.axis === axis)?.title?.en || axis;
    rows.push(
      el("div", { class: "mfilter-axis" }, [
        el("span", { class: "mfilter-name muted", text: title }),
        ...shown.map((value) =>
          el("button", {
            class: `mchip${kept.has(value) ? " on" : ""}`,
            type: "button",
            text: `${axis === "hand" ? HANDS[value].says : value} ${counts.get(value)}`,
            "data-axis": axis,
            title: `${counts.get(value)} exercise${counts.get(value) === 1 ? "" : "s"}`,
            onclick: () => {
              const set = filters.get(axis) || new Set();
              set.has(value) ? set.delete(value) : set.add(value);
              set.size ? filters.set(axis, set) : filters.delete(axis);
              render();
            },
          }),
        ),
        more || wide
          ? el("button", {
              class: "quiet mchip-more",
              type: "button",
              text: more ? `+${more}` : "fewer",
              title: more ? `Show the other ${more}` : "Show fewer",
              onclick: () => {
                wide ? wideOpen.delete(axis) : wideOpen.add(axis);
                render();
              },
            })
          : null,
      ]),
    );
  }
  if (!rows.length) return null;
  return el("div", { class: "mfilters" }, [
    arrivals(),
    ...rows,
    filtering()
      ? el("button", {
          class: "quiet mfilter-clear",
          type: "button",
          text: "Clear filters",
          onclick: () => {
            filters.clear();
            render();
          },
        })
      : null,
  ]);
}

// What has left the course but not the database.
//
// ADR-0006 chose *archived, never deleted* and fought for it -- and then no
// screen, count or route could reach an archived note, which means in practice
// it read as deletion. §4.6 of the design review: "a person who has learned
// that will never use the feature". This is the way back.
function atticPanel() {
  if (!showingAttic) return null;
  if (!attic) return el("p", { class: "muted", text: "Reading the archive…" });

  const staged = (id, kind) => pending.some((c) => c.kind === kind && c.note_id === id);
  const back = async (id, kind, url) => {
    try {
      if (staged(id, kind)) {
        await api("/api/material/discard", {
          method: "POST",
          body: JSON.stringify({ note_id: id, kind }),
        });
      } else {
        await api(url, { method: "POST" });
      }
      pending = (await api("/api/material/pending")).changes;
    } catch (error) {
      toast(`Could not — ${error.message}`, { tone: "bad" });
    }
    render();
  };

  const byUnit = new Map();
  for (const note of attic.notes) {
    if (!byUnit.has(note.unit)) byUnit.set(note.unit, []);
    byUnit.get(note.unit).push(note);
  }

  if (!attic.units.length && !attic.notes.length) {
    return el("div", { class: "mattic" }, [
      el("h3", { class: "mattic-head", text: "Archived" }),
      el("p", { class: "muted", text: "Nothing has been archived. Removing a set puts it here." }),
    ]);
  }

  const setRows = attic.units.map((unit) => {
    const coming = staged(unit.id, "restore_set");
    const held = byUnit.get(unit.id) || [];
    return el("li", { class: `mattic-row${coming ? " coming" : ""}` }, [
      el("span", {
        class: "mattic-name",
        text: unit.title?.en || unit.title?.pl || unit.id,
        title: unit.id,
      }),
      el("span", {
        class: "mattic-when muted",
        text: `set · ${held.length} exercise${held.length === 1 ? "" : "s"} · archived ${said(unit.archived_at)}`,
      }),
      el("button", {
        class: "quiet",
        type: "button",
        text: coming ? "↩ staged" : "Restore",
        title: coming
          ? "Staged to come back — press again to leave it archived"
          : "Bring this set and everything archived with it back, on Confirm",
        onclick: () =>
          back(unit.id, "restore_set", `/api/sets/${encodeURIComponent(unit.id)}/restore`),
      }),
    ]);
  });

  // Notes archived on their own, rather than with the set they sit in. The
  // distinction matters: restoring a shelf says nothing about a note that left
  // for its own reason, so those need their own control.
  const archivedSets = new Set(attic.units.map((u) => u.id));
  const loose = attic.notes.filter((n) => !archivedSets.has(n.unit));
  const noteRows = loose.map((note) => {
    const coming = staged(note.id, "restore");
    return el("li", { class: `mattic-row${coming ? " coming" : ""}` }, [
      el("span", { class: "mattic-name", text: note.label, title: note.id }),
      el("span", {
        class: "mattic-when muted",
        text: `${note.unit} · archived ${said(note.archived_at)}`,
      }),
      el("button", {
        class: "quiet",
        type: "button",
        text: coming ? "↩ staged" : "Restore",
        onclick: async () => {
          if (coming) return back(note.id, "restore", "");
          try {
            await stage(note.id, "restore", true);
            pending = (await api("/api/material/pending")).changes;
          } catch (error) {
            toast(`Could not — ${error.message}`, { tone: "bad" });
          }
          render();
        },
      }),
    ]);
  });

  return el("div", { class: "mattic" }, [
    el("h3", { class: "mattic-head", text: "Archived" }),
    el("p", {
      class: "muted",
      text: "Material that has left the course. Nothing here was deleted — every schedule behind it is still there, and restoring brings both back.",
    }),
    setRows.length ? el("ul", { class: "mattic-list" }, setRows) : null,
    noteRows.length ? el("ul", { class: "mattic-list" }, noteRows) : null,
  ]);
}

// What came in that day, and where it went.
//
// §1.4 of the design review: a lesson does not survive as a thing. One lesson
// file becomes 53 exercises in `gram-preterito-perfeito` and 4 elsewhere, which
// is a reasonable design -- a lesson scatters across thematic sets on purpose --
// but it left no way to ask "what arrived on the 10th?" short of reading the
// YAML. The columns are already the sets, so filtering by a date *is* the
// arrivals view; this is the sentence that reads it back.
function arrivals() {
  const days = filters.get("lesson");
  if (!days?.size) return null;
  const landed = notes.filter((n) => n.lesson && days.has(n.lesson));
  if (!landed.length) return null;
  const sets = new Set(landed.map((n) => n.unit));
  const changed = landed.filter((n) => n.edited_at).length;
  const when = [...days].sort().join(", ");
  return el("p", { class: "marrivals" }, [
    el("strong", { text: `${landed.length} exercise${landed.length === 1 ? "" : "s"}` }),
    el("span", {
      text:
        ` arrived ${when}, across ${sets.size} set${sets.size === 1 ? "" : "s"}` +
        (changed ? ` · ${changed} changed here since` : " · none changed since"),
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
    el("h3", { text: "Capture a lesson" }),
    el("p", {
      class: "muted",
      text: "For a lesson you have not turned into exercises yet. Kept exactly as you type it and queued for an agent — nothing here is studied, counted or checked until one has shaped it and you have confirmed the result. To write exercises yourself, use the Create tab.",
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
            toast(`Not queued — ${error.message}`, { tone: "bad" });
            return;
          }
          composing = false;
          await load();
          toast(
            "Queued. An agent turns it into exercises when you ask — it is in Waiting until then.",
            { tone: "good" },
          );
          document.dispatchEvent(new CustomEvent("repetita:changed"));
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
  const mine = (u) => byUnit.get(u.id) || [];

  // A set whose every exercise is filtered out is not a set you asked about.
  // Keeping the empty column would mean a filter that visibly does nothing.
  const shelves = ordered().filter((u) => !filtering() || mine(u).some(matches));
  if (!banding) {
    return shelves.length
      ? shelves.map((u) => unitColumn(u, mine(u)))
      : [el("p", { class: "muted mempty", text: "Nothing matches those filters." })];
  }

  // Bands keep the board's own order inside them, so dragging a column
  // somewhere still means what it meant -- a band is a heading over the same
  // sequence, not a re-sort.
  const bands = new Map();
  for (const u of shelves) {
    const band = bandOf(u, mine(u)) || "—";
    if (!bands.has(band)) bands.set(band, []);
    bands.get(band).push(u);
  }
  const declared = axes.find((a) => a.axis === banding)?.values || [];
  const order_ = [...bands.keys()].sort((a, b) => {
    const ai = declared.indexOf(a);
    const bi = declared.indexOf(b);
    if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    return a.localeCompare(b);
  });
  return order_.map((band) =>
    el("section", { class: "mband" }, [
      el("h2", { class: "mband-head" }, [
        el("span", { class: "mband-name", text: band }),
        el("span", {
          class: "mband-count muted",
          text: `${bands.get(band).length} set${bands.get(band).length === 1 ? "" : "s"}`,
        }),
      ]),
      el("div", { class: "mband-shelf" }, bands.get(band).map((u) => unitColumn(u, mine(u)))),
    ]),
  );
}

// The board is redrawn on its own so that typing in the search box does not
// rebuild the toolbar under the cursor and lose focus mid-word.
function renderBoard() {
  const board = document.getElementById("mboard");
  if (!board) return render();
  const at = scrollNow();
  board.className = boardClass();
  fill(board, columns());
  scrollBack(at);
}

// A banded board holds bands, an unbanded one holds columns, and at phone width
// those need opposite things: an unbanded board is a horizontal carousel of
// sets, while a banded one stacks its shelves and scrolls *inside* each. CSS
// cannot tell which it is holding, so the board says.
function boardClass() {
  return `mboard${banding ? " banded" : ""}`;
}

function render() {
  const at = scrollNow();
  fill(
    panel,
    toolbar(),
    atticPanel(),
    filterBar(),
    composer(),
    selectionBar(),
    drawer(),
    el("div", { class: "mlayout" }, [
      el("div", { id: "mboard", class: boardClass() }, columns()),
      editor(),
    ]),
  );
  scrollBack(at);
}
