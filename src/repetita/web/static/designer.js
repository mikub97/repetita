// Design my lessons.
//
// A priority list you drag into the order you want, a few knobs, and a preview
// of what tomorrow would look like under it. The preview is the point: the
// selection policy is a pure function, so the server can answer "what would this
// do" without committing to it, and a knob becomes an experiment rather than a
// decision you have to live with.
//
// Nothing here ever sees a card id. `/api/catalogue` returns counts and labels,
// and `/api/plans/<id>/preview` returns how many cards each priority would get
// -- never which ones. Card ids are authored from the material and a quarter of
// them are literally the answer (ADR-0005), so the designer is built to have no
// way of asking for one.

import { api } from "./api.js";
import { el, clear } from "./dom.js";

// The learner's own calendar day, as `app.js` computes it. Sending it is what
// keeps an evening session in one timezone from being filed under another's
// tomorrow.
const today = () => {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

const panel = document.getElementById("designer");
const stage = document.getElementById("stage");
const planBar = document.getElementById("plan-bar");
const tabStudy = document.getElementById("tab-study");
const tabDesign = document.getElementById("tab-design");

// Knobs worth exposing. Each is a real tuning constant with a row in
// docs/tuning.md; the rest stay out of the UI rather than becoming dials nobody
// understands.
const KNOBS = [
  { key: "new_every", label: "New card every", min: 1, max: 10, step: 1, fallback: 3,
    hint: "owed cards between each new one" },
  { key: "batch", label: "Session size", min: 10, max: 100, step: 5, fallback: 40,
    hint: "cards served at once" },
  { key: "template_bias", label: "Harder", min: 0, max: 3, step: 0.5, fallback: 1,
    hint: "weight production over recognition" },
];

let plan = null;
let axes = [];
let rows = [];
let owed = 0;
let dragging = null;

// Three views, two tabs. "practice" is the designer's own session: it lives
// under Design because it is the plan's path, not the course's -- the Study tab
// stays exactly what it always was, and a plan never alters it.
function show(which) {
  const design = which === "design";
  const practice = which === "practice";
  panel.hidden = !design;
  stage.hidden = design;
  planBar.hidden = !practice;
  // Practising a plan is still Design: you got there from the plan, and it is
  // the plan you are exercising.
  const underDesign = design || practice;
  tabDesign.classList.toggle("on", underDesign);
  tabStudy.classList.toggle("on", !underDesign);
  tabDesign.setAttribute("aria-selected", String(underDesign));
  tabStudy.setAttribute("aria-selected", String(!underDesign));
  if (design) load();
}

tabDesign.addEventListener("click", () => show("design"));

// Leaving for Study always means the course's own path. Anything else would
// make "revert to the original" a thing you had to hunt for.
tabStudy.addEventListener("click", () => {
  show("study");
  document.dispatchEvent(new CustomEvent("repetita:restudy", { detail: { plan: null } }));
});

async function load() {
  clear(panel).append(el("p", { class: "muted", text: "Loading…" }));
  try {
    const [catalogue, plans, state] = await Promise.all([
      api("/api/catalogue?group_by=topic"),
      api("/api/plans"),
      api(`/api/state?day=${today()}`),
    ]);
    axes = catalogue.axes;
    rows = catalogue.rows;
    owed = state.owed ?? 0;
    plan = plans.plans.find((p) => p.active) || plans.plans[0] || null;
    if (!plan) plan = await api("/api/plans", {
      method: "POST",
      body: JSON.stringify({ name: "My plan", active: true }),
    });
    render();
    refreshPreview();
  } catch (error) {
    clear(panel).append(el("p", { class: "muted", text: `could not load (${error.message})` }));
  }
}

// --- the priority list ----------------------------------------------------

function priorityRow(p, index) {
  const row = el("li", {
    class: "prio",
    draggable: "true",
    "data-index": String(index),
    ondragstart: (e) => {
      dragging = index;
      e.dataTransfer.effectAllowed = "move";
    },
    ondragover: (e) => {
      e.preventDefault();
      row.classList.add("over");
    },
    ondragleave: () => row.classList.remove("over"),
    ondrop: (e) => {
      e.preventDefault();
      row.classList.remove("over");
      if (dragging === null || dragging === index) return;
      const moved = plan.priorities.splice(dragging, 1)[0];
      plan.priorities.splice(index, 0, moved);
      dragging = null;
      save();
    },
  }, [
    el("span", { class: "grip", text: "⠿", title: "Drag to reorder" }),
    el("span", { class: "prio-rank", text: String(index + 1) }),
    el("span", { class: "prio-name", text: p.value }),
    el("span", { class: "prio-axis", text: p.axis }),
    el("button", {
      class: "quiet", type: "button", text: "×", title: "Remove from the plan",
      onclick: () => {
        plan.priorities.splice(index, 1);
        save();
      },
    }),
  ]);
  return row;
}

function adder() {
  const chosen = new Set(plan.priorities.map((p) => `${p.axis}=${p.value}`));
  const options = rows
    .filter((r) => !chosen.has(`topic=${r.topic}`))
    .sort((a, b) => b.cards - a.cards);
  if (!options.length) return null;

  const select = el("select", { class: "add-topic" }, [
    el("option", { value: "", text: "Add a topic…" }),
    ...options.map((r) =>
      el("option", { value: r.topic, text: `${r.topic} (${r.cards})` }),
    ),
  ]);
  select.addEventListener("change", () => {
    if (!select.value) return;
    plan.priorities.push({ axis: "topic", value: select.value, weight: null });
    save();
  });
  return select;
}

// --- knobs ----------------------------------------------------------------

function knobRow(knob) {
  const value = plan.knobs[knob.key] ?? knob.fallback;
  const out = el("output", { class: "knob-value", text: String(value) });
  const input = el("input", {
    type: "range",
    min: String(knob.min),
    max: String(knob.max),
    step: String(knob.step),
    value: String(value),
  });
  input.addEventListener("input", () => (out.textContent = input.value));
  // Save on release, not on every pixel: dragging a slider would otherwise write
  // a plan revision per frame, and revisions are the record of what was tried.
  input.addEventListener("change", () => {
    plan.knobs[knob.key] = Number(input.value);
    save();
  });
  return el("div", { class: "knob" }, [
    el("label", { class: "knob-label", text: knob.label }),
    input,
    out,
    el("span", { class: "knob-hint muted", text: knob.hint }),
  ]);
}

// --- rendering ------------------------------------------------------------

function render() {
  const list = el("ol", { class: "prios" }, plan.priorities.map(priorityRow));
  const add = adder();

  clear(panel).append(
    el("div", { class: "designer-grid" }, [
      el("section", { class: "pane" }, [
        el("h2", { text: "What matters most" }),
        el("p", { class: "muted", text: "Drag to reorder. The top of the list gets the biggest share of new material." }),
        list,
        add,
      ]),
      el("section", { class: "pane" }, [
        el("h2", { text: "How hard" }),
        ...KNOBS.map(knobRow),
        el("h2", { text: "What you would practise" }),
        // What this does and does not touch, said plainly. A plan practises its
        // own material; the schedule it does not carry stays on the Study tab,
        // where the counter keeps showing it.
        el("p", { class: "muted", text: owed
          ? `Anything owed from these topics comes first. Your other ${owed} owed card${owed === 1 ? "" : "s"} stay on the Study tab — practising here never hides them.`
          : "Nothing owed in these topics, so this is all new material." }),
        el("div", { id: "preview", class: "preview" }, [
          el("p", { class: "muted", text: "…" }),
        ]),
        el("div", { class: "row" }, [
          el("button", {
            class: "primary", type: "button", text: "Practise this plan",
            title: "Study in this order, without changing the Study tab",
            onclick: practise,
          }),
          el("button", {
            class: "quiet", type: "button", text: "Something's off here",
            title: "Record an observation about how the material is organised",
            onclick: raiseIssue,
          }),
        ]),
        el("p", { class: "muted", text: "The Study tab keeps the course's own order and is not affected by any of this. Answers given here count exactly the same." }),
      ]),
    ]),
  );
}

function renderPreview(result) {
  const box = document.getElementById("preview");
  if (!box) return;
  const entries = Object.entries(result.by_priority);
  if (!entries.length) {
    clear(box).append(
      el("p", { class: "muted", text: "No priorities yet — new material follows the course order." }),
    );
    return;
  }
  const total = entries.reduce((n, [, v]) => n + v, 0) || 1;
  clear(box).append(
    ...entries.map(([key, n]) =>
      el("div", { class: "bar-row" }, [
        el("span", { class: "bar-label", text: key }),
        el("span", { class: "bar-track" }, [
          el("span", { class: "bar-fill", style: `width:${Math.round((n / total) * 100)}%` }),
        ]),
        el("span", { class: "bar-n", text: String(n) }),
      ]),
    ),
    result.unplanned
      ? el("p", { class: "muted", text: `${result.unplanned} from elsewhere in the course` })
      : null,
  );
}

async function refreshPreview() {
  try {
    renderPreview(await api(`/api/plans/${plan.id}/preview`, {
      method: "POST",
      body: JSON.stringify({ budget: plan.knobs.batch ?? 20 }),
    }));
  } catch {
    // A preview that cannot be drawn is not worth interrupting the page for.
  }
}

async function save() {
  plan = await api(`/api/plans/${plan.id}`, {
    method: "PUT",
    body: JSON.stringify({ priorities: plan.priorities, knobs: plan.knobs, active: true }),
  });
  render();
  refreshPreview();
}

// Practise the plan, without touching the Study tab.
//
// The same session loop, the same grading, the same scheduling -- only the order
// differs, and the answers count exactly as they would anywhere else. What it
// deliberately does *not* do is change what Study serves: a plan is an
// additional path through the material, and getting back to the course's own
// order should be one click, not a deletion.
function practise() {
  show("practice");
  clear(planBar).append(
    el("span", { class: "plan-bar-name", text: `Practising: ${plan.name}` }),
    el("button", {
      class: "quiet", type: "button", text: "Back to design",
      onclick: () => show("design"),
    }),
    el("button", {
      class: "quiet", type: "button", text: "Leave the plan",
      title: "Back to the course's own order",
      onclick: () => {
        show("study");
        document.dispatchEvent(new CustomEvent("repetita:restudy", { detail: { plan: null } }));
      },
    }),
  );
  document.dispatchEvent(new CustomEvent("repetita:restudy", { detail: { plan: plan.id } }));
}

async function raiseIssue() {
  const body = window.prompt("What looks wrong about how this material is organised?");
  if (!body) return;
  const selector = plan.priorities.map((p) => `${p.axis}=${p.value}`).join(",");
  await api("/api/issues", {
    method: "POST",
    body: JSON.stringify({ body, kind: "other", selector }),
  });
}
