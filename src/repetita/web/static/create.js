// Writing exercises.
//
// The other three tabs work on material that already exists; this one makes it.
// A set down the left, the exercise you are on down the right, and underneath it
// the thing itself -- rendered by the same module the Study tab uses, so the
// preview is the exercise rather than a picture of one.
//
// Two rules are worth knowing before changing anything here:
//
// * **Nothing decides validity in this file.** Whether a field gives away its
//   answer, what an exercise will be called, which forms it can be asked in --
//   all of that comes back from `/api/material/check` as you type. A second
//   opinion in JavaScript would eventually disagree with the one that decides
//   what gets served.
// * **Save applies.** Unlike Manage, there is no staging: writing an exercise
//   has no older version to be careful of, so the button is the confirmation.

import { api } from "./api.js";
import { el, fill, toast } from "./dom.js";
import { show, guardLeaving } from "./designer.js";

import * as choice from "./modes/choice.js";
import * as typein from "./modes/typein.js";
import * as wordbank from "./modes/wordbank.js";
import * as flashcard from "./modes/flashcard.js";

const MODES = Object.fromEntries(
  [choice, typein, wordbank, flashcard].map((mode) => [mode.form, mode]),
);

const panel = document.getElementById("creator");
const tab = document.getElementById("tab-create");

let units = [];
let notes = [];
let shapes = {};
let unit = "";
let title = {};
let rows = [];
let picked = 0;
let checks = [];
let counter = 0;
let previewing = { card: null, form: null };
let loaded = false;
//: The set's description, shown here and edited in Manage (ADR-0013).
let description = {};
//: Somewhere the screen wants to go, held while unsaved work is asked about.
let leaving = null;

tab.addEventListener("click", () => show("create"));

// The one guard the page cannot draw itself. Closing the tab with unsaved
// exercises in it was silent -- nothing here is written until Save, and there
// was no `beforeunload` anywhere in the app.
window.addEventListener("beforeunload", (e) => {
  if (loaded && touched().length) e.preventDefault();
});

document.addEventListener("repetita:view", (e) => {
  if (e.detail?.view !== "create") return;
  const wanted = e.detail.unit;
  // `note` is the exercise you were looking at when you asked to edit it.
  // Opening its set and leaving you to find it again is most of why moving
  // between these two tabs felt like a maze.
  if (!loaded || (wanted && wanted !== unit)) {
    leaveSet(() => load(wanted, e.detail.note));
  } else if (e.detail.note) {
    selectNote(e.detail.note);
  }
});

function selectNote(noteId) {
  const at = rows.findIndex((r) => r.id === noteId);
  if (at === -1) return;
  picked = at;
  previewing = { card: null, form: null };
  render();
}

async function load(wanted, note) {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    const material = await api("/api/material");
    ({ units, notes } = material);
    shapes = material.notetypes;
    loaded = true;
    openSet(wanted || unit || "");
    if (note) selectNote(note);
  } catch (error) {
    fill(panel, el("p", { class: "muted", text: `could not load (${error.message})` }));
  }
}

// Where this screen says things. One function, which is what made moving it out
// of the page footer -- roughly 2,200px below where you are working -- a single
// change rather than eight.
function say(said, tone = "") {
  toast(said, { tone });
}

// --- the set --------------------------------------------------------------

// The type a new exercise gets when nothing suggests otherwise: whatever the
// set already uses most, and failing that the one the course is mostly made of.
function usualType(mine) {
  const counts = new Map();
  for (const note of mine) counts.set(note.notetype, (counts.get(note.notetype) || 0) + 1);
  const [best] = [...counts].sort((a, b) => b[1] - a[1]);
  return best ? best[0] : shapes.gap ? "gap" : Object.keys(shapes)[0];
}

function openSet(id) {
  unit = id;
  const set = units.find((u) => u.id === id);
  title = set ? { ...set.title } : {};
  description = set ? { ...(set.description || {}) } : {};
  const mine = notes.filter((n) => n.unit === id);
  rows = mine.map((note) => {
    const row = {
      key: ++counter,
      id: note.id,
      notetype: note.notetype,
      fields: { ...note.fields },
      tags: [...note.tags],
      label: note.label,
      forms: { ...note.forms },
    };
    // What it looked like when it was opened. Save sends only what differs:
    // rewriting an untouched exercise would mark it edited-here, and from then
    // on a change to it in the course files is a conflict rather than an
    // update. Pressing Save after reading a set must not do that.
    row.clean = JSON.stringify(payload(row));
    return row;
  });
  if (!rows.length) rows = [blank(usualType(mine))];
  picked = 0;
  previewing = { card: null, form: null };
  render();
  recheck();
}

function counted(n) {
  return `${n} exercise${n === 1 ? "" : "s"}`;
}

function blank(notetype) {
  return { key: ++counter, notetype, fields: {}, tags: [], forms: {} };
}

// --- talking to the server ------------------------------------------------

let pendingCheck = null;

// Debounced, because it runs on every keystroke and the answer only matters once
// you stop typing. 400ms is long enough not to chatter and short enough that the
// warning arrives while you are still looking at the field that caused it.
function recheck() {
  clearTimeout(pendingCheck);
  pendingCheck = setTimeout(async () => {
    const live = rows.filter((r) => !r.archived);
    try {
      const body = await api("/api/material/check", {
        method: "POST",
        body: JSON.stringify({ unit, rows: live.map(payload) }),
      });
      checks = new Map(live.map((row, i) => [row.key, body.rows[i]]));
    } catch {
      // A failed check is not a reason to lose what is typed. The screen keeps
      // the last answer it had and Save will refuse loudly if it matters.
      return;
    }
    refresh();
  }, 400);
}

// Changed, new, or on its way out -- the rows Save has anything to do with.
function touched() {
  return rows.filter(
    (row) =>
      row.archived ||
      (!row.id && Object.keys(row.fields).length) ||
      (row.id && JSON.stringify(payload(row)) !== row.clean),
  );
}

function payload(row) {
  const out = {
    notetype: row.notetype,
    fields: row.fields,
    tags: row.tags,
    forms: row.forms,
  };
  if (row.id) out.id = row.id;
  if (row.label) out.label = row.label;
  if (row.lesson) out.lesson = row.lesson;
  if (row.archived) out.archived = true;
  return out;
}

async function save() {
  let report;
  try {
    report = await api(`/api/sets/${encodeURIComponent(unit)}/exercises`, {
      method: "POST",
      body: JSON.stringify({ title, rows: touched().map(payload) }),
    });
  } catch (error) {
    say(`Not saved — ${error.message}. Nothing was written.`, "bad");
    return;
  }

  const said = [];
  if (report.created) said.push(`${report.created} written`);
  if (report.updated) said.push(`${report.updated} changed`);
  if (report.archived) said.push(`${report.archived} removed`);
  if (report.cards_added) said.push(`${report.cards_added} card(s)`);
  if (report.superseded) {
    // It would otherwise come back at the next Confirm, over the top of what
    // was just saved, with nothing said about it.
    said.push(`${report.superseded} staged edit(s) superseded`);
  }
  if (report.quarantined.length) {
    said.push(`⚠ ${report.quarantined.join(", ")} gives away its own answer and will not be served`);
  }
  say(said.join(" · ") || "nothing to save", report.quarantined.length ? "warn" : "good");
  document.dispatchEvent(new CustomEvent("repetita:changed"));

  await load(unit);
}

// --- the rows -------------------------------------------------------------

function nameOf(row, index) {
  const seen = checks instanceof Map ? checks.get(row.key) : null;
  return row.label || seen?.label || `exercise ${index + 1}`;
}

function rowList() {
  return el("ul", { class: "crows" }, [
    ...rows.map((row, index) => {
      const seen = checks instanceof Map ? checks.get(row.key) : null;
      return el(
        "li",
        {
          class: `crow${index === picked ? " on" : ""}${row.archived ? " going" : ""}` +
            `${seen?.leaks?.length ? " leaking" : ""}`,
          onclick: () => {
            picked = index;
            previewing = { card: null, form: null };
            render();
          },
        },
        [
          el("span", { class: "crow-n muted", text: String(index + 1) }),
          el("span", { class: "crow-name", text: nameOf(row, index) }),
          seen?.leaks?.length ? el("span", { class: "crow-warn", text: "⚠" }) : null,
          el("span", {
            class: "crow-drop",
            text: row.archived ? "↩" : "×",
            title: row.archived
              ? "Keep it after all"
              : row.id
                ? "Remove from the course when you save — archived, never deleted"
                : "Discard this one",
            onclick: (e) => {
              e.stopPropagation();
              // One that has never been saved simply goes; one that exists is
              // marked for removal, because removing it is a change to the
              // course and Save is where changes happen.
              if (!row.id) rows = rows.filter((r) => r !== row);
              else row.archived = !row.archived;
              picked = Math.min(picked, Math.max(rows.length - 1, 0));
              render();
              recheck();
            },
          }),
        ],
      );
    }),
  ]);
}

function addRow(notetype) {
  rows.push(blank(notetype || rows[picked]?.notetype || usualType([])));
  picked = rows.length - 1;
  previewing = { card: null, form: null };
  render();
}

function duplicateRow() {
  const from = rows[picked];
  if (!from) return addRow();
  // Without its id: a copy is a new exercise, and sharing an id would mean two
  // exercises sharing one learner's history.
  rows.splice(picked + 1, 0, {
    ...from,
    key: ++counter,
    id: undefined,
    label: "",
    fields: { ...from.fields },
    tags: [...from.tags],
    forms: { ...from.forms },
  });
  picked += 1;
  render();
  recheck();
}

// --- the editor -----------------------------------------------------------

function isList(spec) {
  return spec.type === "text_list";
}

function fieldInput(row, name, spec) {
  const value = row.fields[name];
  const shown = isList(spec) ? (value || []).join(", ") : (value ?? "");
  const long = isList(spec) || String(shown).length > 60;
  const input = el(long ? "textarea" : "input", { class: "cfield-input", rows: "2" });
  input.value = String(shown);
  input.addEventListener("input", () => {
    const raw = input.value;
    row.fields[name] = isList(spec)
      ? raw.split(",").map((v) => v.trim()).filter(Boolean)
      : raw;
    // The list of names and the preview follow what is typed; the inputs
    // themselves are left alone, because rebuilding them takes the cursor with
    // them mid-word.
    refresh();
    recheck();
  });
  return el("div", { class: "cfield" }, [
    el("label", { class: "cfield-label" }, [
      el("span", { text: name + (spec.required ? " *" : "") }),
      el("span", {
        class: `cfield-when ${spec.visibility}`,
        text: spec.visibility === "before" ? "shown with the question" : "shown after answering",
      }),
    ]),
    input,
  ]);
}

function typePicker(row) {
  const select = el("select", { class: "ctype" }, [
    ...Object.keys(shapes).map((name) => el("option", { value: name, text: name })),
  ]);
  select.value = row.notetype;
  select.addEventListener("change", () => {
    row.notetype = select.value;
    // A field the new type does not have cannot be saved -- but deleting what
    // you typed, with no warning and no way back, is not the answer either. It
    // is set aside, and comes back if the type does.
    const known = new Set(Object.keys(shapes[row.notetype].fields));
    row.stash = row.stash || {};
    for (const name of Object.keys(row.fields)) {
      if (!known.has(name)) {
        row.stash[name] = row.fields[name];
        delete row.fields[name];
      }
    }
    for (const name of Object.keys(row.stash)) {
      if (known.has(name) && !row.fields[name]) {
        row.fields[name] = row.stash[name];
        delete row.stash[name];
      }
    }
    row.forms = {};
    previewing = { card: null, form: null };
    render();
    recheck();
  });
  return select;
}

// --- how it is asked ------------------------------------------------------

// One group per card the exercise will make. Five of the six types have exactly
// one; `vocab` has three, and they are genuinely different questions about the
// same note -- so they get their own line rather than one setting pretending to
// cover them.
function askedAs(row) {
  const shape = shapes[row.notetype];
  const seen = checks instanceof Map ? checks.get(row.key) : null;
  const cards = cardsOf(row);
  if (!cards.length) return null;
  return el("div", { class: "casked" }, [
    el("h4", { text: cards.length > 1 ? "asked as" : "asked as", class: "csection" }),
    ...cards.map((template) => {
      const card = shape.cards[template];
      const why = seen?.forms?.[template] || {};
      const chosen = row.forms[template] || card.forms;
      return el("div", { class: "casked-card" }, [
        cards.length > 1 ? el("span", { class: "casked-which muted", text: template }) : null,
        el(
          "div",
          { class: "casked-forms" },
          card.askable.map((form) => {
            const blocked = why[form];
            const on = chosen.includes(form);
            return el("button", {
              class: `cform${on ? " on" : ""}${blocked ? " blocked" : ""}`,
              type: "button",
              text: form,
              // The reason, not a disabled button: a form that silently cannot
              // be picked teaches nobody the rule behind it.
              title: blocked || `Ask this exercise as a ${form}`,
              onclick: () => {
                const next = new Set(chosen);
                next.has(form) ? next.delete(form) : next.add(form);
                // Back to the type's own choice rather than none at all: an
                // exercise with no form is one nothing can render.
                if (!next.size) delete row.forms[template];
                else row.forms[template] = card.forms.filter((f) => next.has(f))
                  .concat([...next].filter((f) => !card.forms.includes(f)));
                render();
                recheck();
              },
            });
          }),
        ),
        ...Object.entries(why)
          .filter(([form, reason]) => reason && chosen.includes(form))
          .map(([form, reason]) =>
            el("p", { class: "cblocked", text: `${form}: ${reason}` }),
          ),
      ]);
    }),
  ]);
}

// --- the preview ----------------------------------------------------------

// A card payload shaped exactly like `serialize.public_card`, built from what is
// being typed, so the real form module can render it. `visible_before` comes
// from the server with the note type -- this file does not decide what a learner
// may see, it only draws it.
function asCard(row, template, form, seen) {
  const card = shapes[row.notetype].cards[template];
  const answers = row.fields[card.expect];
  const first = Array.isArray(answers) ? answers[0] || "" : String(answers || "");
  const fields = {};
  for (const name of card.visible_before) {
    if (row.fields[name]) fields[name] = row.fields[name];
  }
  return {
    id: "preview",
    notetype: row.notetype,
    template,
    form,
    ask: card.ask.filter((name) => row.fields[name]),
    fields,
    tokens: first.split(" ").filter(Boolean),
    // The options the server would actually offer -- the wrong ones are mined
    // from the rest of the course and this side does not have them. Before the
    // first check comes back, what is typed is the best that can be shown.
    options: seen?.options?.[template]?.length
      ? seen.options[template]
      : [first, ...(row.fields.distractors || [])].filter(Boolean),
  };
}

// Which cards this exercise would make.
//
// An empty array from the server means "none yet -- there is no answer in it",
// which is not the same as not having asked: before the first check comes back
// the note type's own list is the honest guess.
function cardsOf(row) {
  const seen = checks instanceof Map ? checks.get(row.key) : null;
  return seen ? seen.cards || [] : shapes[row.notetype].card_order;
}

function preview(row) {
  const shape = shapes[row.notetype];
  const seen = checks instanceof Map ? checks.get(row.key) : null;
  const cards = cardsOf(row);
  if (!cards.length) {
    const answer = shape.cards[shape.card_order[0]].expect;
    return el("p", {
      class: "muted",
      text: `Fill in ${answer} and this becomes an exercise.`,
    });
  }
  const template = cards.includes(previewing.card) ? previewing.card : cards[0];
  const chosen = row.forms[template] || shape.cards[template].forms;
  const why = seen?.forms?.[template] || {};
  const usable = chosen.filter((f) => !why[f] && MODES[f]);
  const form = usable.includes(previewing.form) ? previewing.form : usable[0];

  return el("div", { class: "cpreview-box" }, [
    el("div", { class: "cpreview-tabs" }, [
      ...cards.map((name) =>
        cards.length > 1
          ? el("button", {
              class: `ctab${name === template ? " on" : ""}`,
              type: "button",
              text: name,
              onclick: () => {
                previewing = { card: name, form: null };
                refresh();
              },
            })
          : null,
      ),
      ...usable.map((name) =>
        usable.length > 1
          ? el("button", {
              class: `ctab${name === form ? " on" : ""}`,
              type: "button",
              text: name,
              onclick: () => {
                previewing = { card: template, form: name };
                refresh();
              },
            })
          : null,
      ),
    ]),
    form
      ? el("div", {}, [
          el("div", { class: "cpreview-card" }, [
            // The study renderer, not a drawing of it. `submit` does nothing:
            // this is what the exercise looks like, not a place to answer it.
            MODES[form].render(asCard(row, template, form, seen), () => {}),
          ]),
          el("p", { class: "muted cpreview-note", text: "Nothing here is graded." }),
        ])
      : el("p", {
          class: "muted",
          text: "Nothing can ask this yet — see the reasons above.",
        }),
  ]);
}

// --- pasting a list -------------------------------------------------------

// What a pasted line maps onto, said on screen rather than guessed at: the
// required fields of the chosen type, in the order the type declares them.
function pasteInto(row) {
  const shape = shapes[row.notetype];
  const required = shape.order.filter((name) => shape.fields[name].required);
  const box = el("textarea", {
    class: "cpaste-box",
    rows: "6",
    placeholder: required.join(" | ") + "\none exercise per line",
  });
  return el("div", { class: "cpaste" }, [
    el("p", { class: "muted", text: `One per line, ${required.join(" | ")}` }),
    box,
    el("div", { class: "row" }, [
      el("button", {
        class: "quiet",
        type: "button",
        text: "Add them",
        onclick: () => {
          const made = box.value
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean)
            .map((line) => {
              const parts = line.split("|").map((p) => p.trim());
              const row_ = blank(row.notetype);
              required.forEach((name, i) => {
                const value = parts[i] || "";
                if (!value) return;
                row_.fields[name] = isList(shape.fields[name])
                  ? value.split(",").map((v) => v.trim()).filter(Boolean)
                  : value;
              });
              return row_;
            });
          if (!made.length) return;
          rows = [...rows.filter((r) => r.id || Object.keys(r.fields).length), ...made];
          picked = rows.length - 1;
          render();
          recheck();
        },
      }),
    ]),
  ]);
}

// --- the screen -----------------------------------------------------------

// Which set you are working on, and -- separately -- what it is called.
//
// These were one control and one trap: picking an existing set and then typing
// in the id box did not rename anything, it quietly pointed Save at a different
// set, so the next edit moved that one exercise into a set that did not exist
// and left the rest behind. Choosing and naming are now two acts, and renaming
// goes through the endpoint that moves every note in one transaction.
function header() {
  const known = units.some((u) => u.id === unit);

  const picker = el("select", { class: "cset" }, [
    el("option", { value: "", text: "New set…" }),
    ...units.map((u) => el("option", { value: u.id, text: u.id })),
  ]);
  picker.value = known ? unit : "";
  picker.addEventListener("change", () => leaveSet(() => openSet(picker.value)));

  const count = el("span", {
    id: "cheader-count",
    class: "muted ccount",
    text: counted(rows.filter((r) => !r.archived).length),
  });

  if (known) {
    const named = title.en || title.pl || "";
    return el("div", { class: "cheader" }, [
      el("label", { class: "cfield-label", text: "set" }),
      picker,
      named
        ? el("span", {
            class: "cset-title-said",
            text: named,
            title: description.en || description.pl || "",
          })
        : el("span", { class: "cset-title-said unnamed", text: "no name yet" }),
      // Naming lives in Manage, staged with everything else there (ADR-0013).
      // This tab writes exercises; it stopped being the place where a set's
      // existence is edited, because removing one never was.
      el("button", {
        class: "quiet",
        type: "button",
        text: named ? "Name & describe…" : "Name this set…",
        title: "Sets are named in Manage, where they are looked at — and staged there like every other change",
        onclick: () => leaveSet(() => show("manage", { name: unit })),
      }),
      count,
    ]);
  }

  // Naming a set that does not exist yet. Creating is not renaming: this one is
  // typed here because there is nothing in Manage to click on yet.
  const target = { id: unit, title };
  const id = el("input", { class: "cset-id", placeholder: "licao-2026-09-18" });
  id.value = target.id;
  id.addEventListener("input", () => {
    target.id = id.value.trim();
    unit = target.id;
    refresh();
  });

  const label = el("input", { class: "cset-title", placeholder: "A name for it" });
  label.value = target.title.en || target.title.pl || "";
  label.addEventListener("input", () => {
    target.title = label.value.trim() ? { en: label.value.trim() } : {};
    title = target.title;
    refresh();
  });

  return el("div", { class: "cheader" }, [
    el("label", { class: "cfield-label", text: "new set" }),
    picker,
    id,
    label,
    count,
  ]);
}

// Unsaved work does not evaporate because you looked at another set -- and it
// does not trap you here either. Every way out routes through this: choosing
// another set, clicking another tab, or saying plainly that you want rid of it.
function leaveSet(go) {
  if (!touched().length) return go();
  leaving = go;
  render();
}

// Leaving the tab itself. Without this the rows stayed in memory and came back
// the next time you opened Create, which is kind until the day you wanted them
// gone and had nowhere to say so.
guardLeaving((which) => {
  if (!loaded || !touched().length) return true;
  leaveSet(() => show(which));
  return false;
});

// Asked through the same bar as every other way out, so "discard" means one
// thing on this screen rather than two.
function discardAll() {
  leaveSet(() => {});
}

function editor() {
  const row = rows[picked];
  if (!row) return el("p", { class: "muted", text: "Add an exercise to begin." });
  const shape = shapes[row.notetype];
  const seen = checks instanceof Map ? checks.get(row.key) : null;
  const specs = shape.order.map((name) => [name, shape.fields[name]]);
  const required = specs.filter(([, s]) => s.required);
  const optional = specs.filter(([, s]) => !s.required);

  const tags = el("input", { class: "cfield-input", placeholder: "A2, comida" });
  tags.value = row.tags.join(", ");
  tags.addEventListener("input", () => {
    row.tags = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    // Like every other field. Without these the footer never recomputed, so
    // changing only the tags left Save greyed out and the screen looking as
    // though it had not noticed.
    refresh();
    recheck();
  });

  const name = el("input", { class: "cfield-input", placeholder: seen?.label || "" });
  name.value = row.label || "";
  name.addEventListener("input", () => {
    row.label = name.value.trim();
    refresh();
  });

  // Two panes: what the exercise says on the left, what it does on the right.
  // The preview belongs beside the fields rather than under them -- the whole
  // point of it is watching a field move as you type into it.
  return el("div", { class: "ceditor" }, [
    el("div", { class: "cwrite" }, [
      el("div", { class: "row" }, [typePicker(row), el("span", { id: "cmakes" }, [makes(row)])]),
      ...required.map(([name_, spec]) => fieldInput(row, name_, spec)),
      el("details", { class: "cmore" }, [
        el("summary", { text: `more: ${optional.map(([n]) => n).join(" · ")}` }),
        ...optional.map(([name_, spec]) => fieldInput(row, name_, spec)),
        el("div", { class: "cfield" }, [
          el("label", { class: "cfield-label" }, [el("span", { text: "tags" })]),
          tags,
        ]),
        el("div", { class: "cfield" }, [
          el("label", { class: "cfield-label" }, [
            el("span", { text: "name" }),
            el("span", {
              class: "cfield-when muted",
              text: "derived from the answer unless you say",
            }),
          ]),
          name,
        ]),
      ]),
    ]),
    el("div", { class: "cshow" }, [
      el("div", { id: "casked" }, [askedAs(row)]),
      el("h4", { class: "csection", text: "as the learner sees it" }),
      el("div", { id: "cpreview" }, [preview(row)]),
    ]),
  ]);
}

function makes(row) {
  const n = cardsOf(row).length;
  return el("span", {
    class: "muted",
    text: n ? `makes ${n} card${n === 1 ? "" : "s"}` : "not an exercise yet",
  });
}

// `[leak] : card 'fill': ...` is how a problem prints for the CLI, where the
// tag is the only thing marking severity. Here the colour does that, and the
// tag reads as noise in the middle of a sentence.
function plain(said, row) {
  const without = String(said).replace(/^\[[a-z]+\]\s*:?\s*/i, "");
  // And the id, when the note has one: the line already begins with the name.
  return row?.id && without.startsWith(`${row.id}: `)
    ? without.slice(row.id.length + 2)
    : without;
}

function problems() {
  if (!(checks instanceof Map)) return null;
  const said = [];
  for (const [index, row] of rows.entries()) {
    const seen = checks.get(row.key);
    if (!seen) continue;
    const name = nameOf(row, index);
    if (seen.refused) said.push(el("p", { class: "cleak", text: `${name}: ${seen.refused}` }));
    for (const leak of seen.leaks || []) {
      said.push(el("p", { class: "cleak", text: `${name}: ${plain(leak, row)}` }));
    }
    for (const warn of seen.warnings || []) {
      said.push(el("p", { class: "cwarn muted", text: `${name}: ${plain(warn, row)}` }));
    }
  }
  return said.length ? el("div", { class: "cproblems" }, said) : null;
}

// Asked, not assumed. Switching sets rebuilt the rows from the server and
// everything typed since the last Save went with them, silently.
function unsaved() {
  if (!leaving) return null;
  const work = touched();
  return el("div", { class: "cleaving" }, [
    el("span", { text: `${counted(work.length)} not saved.` }),
    el("button", {
      class: "primary",
      type: "button",
      text: "Save them first",
      onclick: async () => {
        const go = leaving;
        leaving = null;
        await save();
        go();
      },
    }),
    el("button", {
      class: "quiet",
      type: "button",
      text: "Discard them",
      onclick: () => {
        const go = leaving;
        leaving = null;
        // Actually put the rows back, and only then go. Leaving them in memory
        // means the guard stops you again on the way out, over work you just
        // said you did not want.
        openSet(unit);
        go();
      },
    }),
    el("button", {
      class: "quiet",
      type: "button",
      text: "Stay here",
      onclick: () => {
        leaving = null;
        render();
      },
    }),
  ]);
}

function footer() {
  const work = touched();
  const going = work.filter((r) => r.archived).length;
  const fresh = work.filter((r) => !r.id).length;
  // A set that does not exist yet is itself something to save, so naming one
  // and pressing Save makes it -- which is what "+ New set" used to do and what
  // this screen could not do at all.
  const newSet = Boolean(unit) && !units.some((u) => u.id === unit);
  const said = [
    newSet ? "a new set" : null,
    fresh ? `${fresh} new` : null,
    work.length - fresh - going ? `${work.length - fresh - going} changed` : null,
    going ? `${going} to remove` : null,
  ].filter(Boolean);
  return el("div", { class: "cfooter" }, [
    el("button", {
      class: "primary",
      type: "button",
      text: "Save",
      disabled: unit && (work.length || newSet) ? null : "disabled",
      onclick: save,
    }),
    work.length
      ? el("button", {
          class: "quiet",
          type: "button",
          text: "Discard changes",
          title: "Put this set back the way it was saved",
          onclick: discardAll,
        })
      : null,
    el("span", {
      class: "muted",
      text: unit ? `${said.join(" · ") || "nothing to save"} → ${unit}` : "name the set first",
    }),
    // The other half of the promise Manage states on its toolbar. Two commit
    // models are liveable; two unstated ones are not.
    el("span", { class: "muted mpromise", text: "· saves straight away" }),
  ]);
}

// Everything that changes as you type, and nothing that holds a cursor.
//
// The editor's inputs are deliberately not rebuilt -- doing that takes the caret
// with them mid-word -- but everything derived from them is, or the screen goes
// on saying "not an exercise yet" about an exercise you have just finished.
function refresh() {
  const row = rows[picked];
  const list = document.getElementById("crows");
  if (list) fill(list, rowList());
  const makesBox = document.getElementById("cmakes");
  if (makesBox && row) fill(makesBox, makes(row));
  const asked = document.getElementById("casked");
  if (asked && row) fill(asked, askedAs(row));
  const box = document.getElementById("cpreview");
  if (box && row) fill(box, preview(row));
  const said = document.getElementById("cproblems");
  if (said) fill(said, problems());
  const foot = document.getElementById("cfooter");
  if (foot) fill(foot, footer());
  const head = document.getElementById("cheader-count");
  if (head) head.textContent = counted(rows.filter((r) => !r.archived).length);
}

function render() {
  fill(
    panel,
    header(),
    unsaved(),
    el("div", { class: "clayout" }, [
      el("div", { class: "cside" }, [
        el("div", { id: "crows" }, [rowList()]),
        el("div", { class: "row" }, [
          el("button", { class: "quiet", type: "button", text: "+ add", onclick: () => addRow() }),
          el("button", {
            class: "quiet",
            type: "button",
            text: "⧉ duplicate",
            onclick: duplicateRow,
          }),
        ]),
        rows[picked] ? pasteInto(rows[picked]) : null,
      ]),
      el("div", { class: "cmain" }, [editor()]),
    ]),
    el("div", { id: "cproblems" }, [problems()]),
    el("div", { id: "cfooter" }, [footer()]),
  );
}
