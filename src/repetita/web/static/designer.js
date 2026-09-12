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
import { el, clear, dot, masteryBar, fill, toast } from "./dom.js";

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
// The knobs worth exposing, each a real tuning constant with a row in
// docs/tuning.md. They read as sentences that update, because `template_bias`
// with a slider from 0 to 3 tells you the name of a variable and nothing about
// what moving it does.
const KNOBS = [
  {
    key: "new_every", min: 1, max: 10, step: 1, fallback: 3,
    says: (v) => `One new card after every ${v} you already owe.`,
  },
  {
    key: "batch", min: 10, max: 100, step: 5, fallback: 40,
    says: (v) => `Sessions of about ${v} cards.`,
  },
  {
    key: "template_bias", min: 0, max: 3, step: 0.5, fallback: 1,
    says: (v) =>
      v > 1.2
        ? "Lean towards producing the word, which is harder and sticks better."
        : v < 0.8
          ? "Lean towards recognising the word, which is gentler."
          : "Recognising and producing in equal measure.",
  },
];

let plan = null;
let axes = [];
let rows = [];
let mastery = {};
let owed = 0;
let dragging = null;

// Six views, five tabs, one row each.
//
// This was four booleans and eight `classList.toggle` lines for three views,
// and adding a fourth by the same method is how it gets to six. `practice` is
// the designer's own session: it has no panel of its own and leaves Design's tab
// lit, because it is the plan's path rather than the course's -- the Study tab
// stays exactly what it always was, and a plan never alters it.
const VIEWS = {
  // `shell` is what `body[data-tab]` becomes, and the page sizes itself from it:
  // Study wants a short line, Design two panes, Create a wide desk, Manage the
  // whole monitor.
  study: { panel: null, tab: "tab-study", shell: "study", hash: "" },
  practice: { panel: null, tab: "tab-design", shell: "study", hash: "" },
  design: { panel: "designer", tab: "tab-design", shell: "design", hash: "#design" },
  create: { panel: "creator", tab: "tab-create", shell: "create", hash: "#create" },
  manage: { panel: "manager", tab: "tab-manage", shell: "manage", hash: "#manage" },
  // The whole monitor, like Manage: it is a table of 18 columns.
  admin: { panel: "admin", tab: "tab-admin", shell: "manage", hash: "#admin" },
  // No tab of its own: it is reached from the header, and lighting up a tab that
  // is not there would leave every tab looking unselected.
  waiting: { panel: "waiting", tab: "", shell: "design", hash: "#waiting" },
};

const PANELS = [...new Set(Object.values(VIEWS).map((v) => v.panel))].filter(Boolean);
const TABS = [...new Set(Object.values(VIEWS).map((v) => v.tab))].filter(Boolean);

//: A view that may ask before it is left. Only Create sets one: everything it
//: holds lives in memory until Save, so walking away is how work disappears --
//: and having no way to walk away *deliberately* is how it piles up.
let guard = null;
let here = "study";

export function guardLeaving(fn) {
  guard = fn;
}

export function show(which, detail = {}) {
  // The guard decides. It either lets this through now or asks, and calls
  // `show` again itself with the answer.
  if (guard && which !== here && !guard(which, detail)) return;
  here = which;
  const view = VIEWS[which] || VIEWS.study;
  if (location.hash !== view.hash) {
    history.replaceState(null, "", view.hash || location.pathname);
  }
  for (const id of PANELS) document.getElementById(id).hidden = id !== view.panel;
  stage.hidden = Boolean(view.panel);
  planBar.hidden = which !== "practice";
  document.body.dataset.tab = view.shell;
  for (const id of TABS) {
    const on = id === view.tab;
    document.getElementById(id).classList.toggle("on", on);
    document.getElementById(id).setAttribute("aria-selected", String(on));
  }
  // Announced rather than called, because the tabs are separate modules and
  // `show` should not have to know which of them needs waking. Clicking a tab
  // and arriving on it by URL then take the same path -- the bug that came from
  // having two was Manage rendering an empty page when linked to directly.
  document.dispatchEvent(new CustomEvent("repetita:view", { detail: { view: which, ...detail } }));
  if (which === "design") load();
}

tabDesign.addEventListener("click", () => show("design"));

// Which tab you are on survives a reload and can be linked to. Small thing, but
// "let me show you this" currently means "click Manage after it loads".
function fromHash() {
  const want = (location.hash || "").replace("#", "");
  if (want && want !== "study" && VIEWS[want]) show(want);
}

window.addEventListener("hashchange", fromHash);

// A task, not a microtask. Each `<script type="module">` is evaluated as its own
// job and microtasks drain between them, so a microtask queued here runs before
// `manage.js` has registered its listener -- which showed up as the Manage tab
// opening to a blank page when it was linked to directly, and working fine when
// it was clicked.
setTimeout(fromHash, 0);

// Leaving for Study always means the course's own path. Anything else would
// make "revert to the original" a thing you had to hunt for.
tabStudy.addEventListener("click", () => {
  show("study");
  document.dispatchEvent(new CustomEvent("repetita:restudy", { detail: { plan: null } }));
});

// What is typed into the material search, and the timer that debounces it. The
// search runs in SQL because the answer is a count per topic -- this tab has
// never held the material itself, and it is not going to start.
let search = "";
let searching = null;

async function load() {
  fill(panel, el("p", { class: "muted", text: "Loading…" }));
  try {
    const [catalogue, plans, state] = await Promise.all([
      api(`/api/catalogue?group_by=topic${search ? `&q=${encodeURIComponent(search)}` : ""}`),
      api("/api/plans"),
      api(`/api/state?day=${today()}`),
    ]);
    axes = catalogue.axes;
    rows = catalogue.rows;
    mastery = catalogue.mastery || {};
    owed = state.owed ?? 0;
    plan = plans.plans.find((p) => p.active) || plans.plans[0] || null;
    if (!plan) plan = await api("/api/plans", {
      method: "POST",
      body: JSON.stringify({ name: "My plan", active: true }),
    });
    render();
    refreshPreview();
  } catch (error) {
    fill(panel, el("p", { class: "muted", text: `could not load (${error.message})` }));
  }
}

// --- the priority list ----------------------------------------------------

// The share a row actually gets, so "top of the list" has a number attached.
// Mirrors `weights_from_ranks` in policies/planned.py: 1/rank, normalised, with
// an explicit weight overriding its rank.
function shares(priorities) {
  const pinned = priorities.map((p) => (p.weight == null ? 0 : p.weight));
  const spare = Math.max(0, 1 - pinned.reduce((a, b) => a + b, 0));
  const free = priorities.map((p, i) => (p.weight == null ? 1 / (i + 1) : 0));
  const total = free.reduce((a, b) => a + b, 0);
  return priorities.map((p, i) =>
    p.weight != null ? p.weight : total ? (spare * free[i]) / total : 0,
  );
}

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
    el("span", { class: "prio-share" }, [
      el("span", {
        class: "prio-share-fill",
        style: `width:${Math.round(shares(plan.priorities)[index] * 100)}%`,
      }),
    ]),
    el("span", {
      class: "prio-pct muted",
      text: `${Math.round(shares(plan.priorities)[index] * 100)}%`,
    }),
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

// Everything you could choose, and how far into it you are.
//
// It replaces a dropdown. A dropdown asks you to remember what exists; this
// shows you, with the size and the progress, so the decision is made by looking
// rather than by recalling.
function topicCard(row) {
  const value = row.topic;
  const m = mastery[value];
  const chosen = plan.priorities.some((p) => p.axis === "topic" && p.value === value);
  return el(
    "li",
    {
      class: `topic${chosen ? " chosen" : ""}`,
      draggable: chosen ? "false" : "true",
      ondragstart: () => (dragging = { axis: "topic", value }),
      onclick: () => (chosen ? null : addPriority("topic", value)),
      title: chosen ? "already in the plan" : "add to the plan",
    },
    [
      el("div", { class: "topic-head" }, [
        dot(m ? m.state : "untouched", m ? `${Math.round(m.progress * 100)}% started` : ""),
        el("span", { class: "topic-name", text: value }),
        el("span", {
          class: "topic-count muted",
          // While searching, the total is the context that makes the match
          // count mean something: three of twelve is a corner of a topic,
          // three of four is most of it.
          text: row.matched === undefined ? String(row.cards) : `${row.matched} / ${row.notes}`,
          title: row.matched === undefined
            ? `${row.cards} cards`
            : `${row.matched} of ${row.notes} exercises match "${search}"`,
        }),
      ]),
      masteryBar(m),
    ],
  );
}

function searchBox() {
  const field = el("input", {
    class: "topic-search",
    type: "search",
    value: search,
    placeholder: "Search exercises, answers, tags…",
    // Debounced: this is a query per keystroke otherwise, and the answer for
    // "fe" is never the one anybody wanted.
    oninput: (event) => {
      const typed = event.target.value;
      clearTimeout(searching);
      searching = setTimeout(() => {
        if (typed === search) return;
        search = typed;
        load();
      }, 250);
    },
  });
  // Focus survives the re-render that a search triggers, so typing is not
  // interrupted by its own result arriving.
  setTimeout(() => {
    const live = panel.querySelector(".topic-search");
    if (live && search) {
      live.focus();
      live.setSelectionRange(live.value.length, live.value.length);
    }
  }, 0);
  return field;
}

function materialPane() {
  const sorted = [...rows].sort((a, b) => b.cards - a.cards);
  const said = search
    ? sorted.length
      ? `${sorted.length} topic${sorted.length === 1 ? " holds" : "s hold"} "${search}".`
      : `Nothing in this course matches "${search}".`
    : "Click or drag a topic into the plan. The bar is how much of it you have started; a hatched stripe is material you marked known rather than learned.";

  return el("section", { class: "pane" }, [
    el("h2", { text: "Your material" }),
    searchBox(),
    el("p", { class: "muted", text: said }),
    el("ul", { class: "topics" }, sorted.map(topicCard)),
  ]);
}

async function addPriority(axis, value) {
  plan.priorities.push({ axis, value, weight: null });
  await save();
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
  const say = el("p", { class: "knob-says", text: knob.says(value) });
  const input = el("input", {
    type: "range",
    min: String(knob.min),
    max: String(knob.max),
    step: String(knob.step),
    value: String(value),
  });
  input.addEventListener("input", () => (say.textContent = knob.says(Number(input.value))));
  // Save on release, not on every pixel: dragging a slider would otherwise
  // write a plan revision per frame, and revisions are the record of what was
  // tried.
  input.addEventListener("change", () => {
    plan.knobs[knob.key] = Number(input.value);
    save();
  });
  return el("div", { class: "knob" }, [say, input]);
}

// --- rendering ------------------------------------------------------------

function render() {
  const list = el("ol", {
    class: "prios",
    // Dropping a topic from the material pane onto the list adds it. The same
    // gesture that reorders also recruits, which is what "drag it in" means.
    ondragover: (e) => e.preventDefault(),
    ondrop: (e) => {
      e.preventDefault();
      if (dragging && dragging.axis) {
        const { axis, value } = dragging;
        dragging = null;
        if (!plan.priorities.some((p) => p.axis === axis && p.value === value)) {
          addPriority(axis, value);
        }
      }
    },
  }, plan.priorities.map(priorityRow));

  fill(panel, 
    el("div", { class: "designer-grid" }, [
      materialPane(),
      el("section", { class: "pane" }, [
        el("h2", { text: "Your plan" }),
        plan.priorities.length
          ? el("p", { class: "muted", text: "Drag to reorder. The top of the list gets the biggest share of new material." })
          : el("p", { class: "muted", text: "Nothing chosen yet — pick a topic on the left and it will appear here." }),
        list,
        el("h2", { text: "How hard" }),
        ...KNOBS.map(knobRow),
        el("h2", { text: "What you would practise" }),
        el("p", { class: "muted", text: owed
          ? `Anything owed from these topics comes first. Your other ${owed} owed card${owed === 1 ? "" : "s"} stay on the Study tab — practising here never hides them.`
          : "Nothing owed in these topics, so this is all new material." }),
        el("div", { id: "preview", class: "preview" }, [
          el("p", { class: "muted", text: "…" }),
        ]),
        el("div", { class: "row" }, [
          el("button", {
            class: "primary",
            type: "button",
            text: "Practise this plan",
            // An empty plan is not a plan. With no priorities the policy has no
            // mix to honour and falls back to serving whatever comes next --
            // which is the Study tab, arrived at by a button that claims to be
            // something else.
            disabled: plan.priorities.length ? null : "disabled",
            title: plan.priorities.length
              ? "Study in this order, without changing the Study tab"
              : "Choose at least one topic first",
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
    fill(box, 
      el("p", { class: "muted", text: "No priorities yet — new material follows the course order." }),
    );
    return;
  }
  const total = entries.reduce((n, [, v]) => n + v, 0) || 1;
  fill(box, 
    ...entries.map(([key, n]) =>
      el("div", { class: "bar-row" }, [
        // `topic=comida` is how the server addresses it; "comida" is what it is
        // called. Selector syntax in an interface is a leaked implementation.
        el("span", { class: "bar-label", text: key.includes("=") ? key.split("=")[1] : key }),
        el("span", { class: "bar-track" }, [
          el("span", { class: "bar-fill", style: `width:${Math.round((n / total) * 100)}%` }),
        ]),
        el("span", { class: "bar-n", text: String(n) }),
      ]),
    ),
    result.unplanned
      ? el("p", { class: "muted", text: `${result.unplanned} from elsewhere in the course` })
      : null,
    names(result.names),
  );
}

// Which exercises, not only how many.
//
// The sample is shuffled by the server on every call, so what you read here is
// not the order you will be asked in -- see `_preview_names` in api.py for why
// that is a mitigation rather than a fix, and ADR-0008 for why it is accepted.
function names(list) {
  if (!list || !list.length) return null;
  return el("div", { class: "preview-names" }, [
    el("p", { class: "muted", text: "for example" }),
    el("ul", { class: "namelist" }, list.map((n) => el("li", { text: n }))),
  ]);
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
  // Belt and braces: the button is disabled, but `practise` is also reachable
  // from a stale render, and serving "the plan" when there is no plan is worse
  // than doing nothing.
  if (!plan.priorities.length) return;
  show("practice");
  fill(planBar, 
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
  try {
    await api("/api/issues", {
      method: "POST",
      body: JSON.stringify({ body, kind: "other", selector }),
    });
  } catch (error) {
    // There was no `catch`: a failure here was an unhandled rejection in the
    // console and nothing at all on screen.
    toast(`Not filed — ${error.message}`, { tone: "bad" });
    return;
  }
  // And nothing said so on success either, so filing one felt like typing into
  // a void -- which it was, since no screen listed them until Waiting.
  toast("Filed. It is in Waiting, and `repetita issues` lists it.", { tone: "good" });
  document.dispatchEvent(new CustomEvent("repetita:changed"));
}
