// Jak się uczę: how the Study tab builds its queue.
//
// A different thing from a study plan, and deliberately a different screen
// (ADR-0017). A plan is an additional path through the material, asked for when
// you press "practise this"; this is how the one path everybody already has is
// ordered, and it applies until you say otherwise.
//
// Named on the surface, spelled out underneath. That is the house argument --
// `settings.js` names three themes rather than offering a font-size slider --
// but it is not the whole answer here, because you asked for full
// configurability: the preset list is the door, and `<details>` is the room.
//
// The preview is the point, the same way it is the point in the designer: the
// policies are pure, so the server can answer "what would tomorrow look like"
// without committing to anything, and a setting becomes an experiment rather
// than a decision you have to live with. The forecast beside it is the other
// half -- a knob that grows your backlog should say so while you are turning it,
// not six weeks later.

import { api } from "./api.js";
import { el, fill, toast } from "./dom.js";
import { show } from "./designer.js";
import { KNOBS, SWITCHES } from "./modes.js";

const panel = document.getElementById("howstudy");
const link = document.getElementById("how-link");

// Name and why-sentence per mode. The recipe lives on the server
// (`store/styles.MODES`) because it is behaviour; these are UI text.
const SAID = {
  kurs: ["Po kolei", "Kurs prowadzi. Świeża lekcja wchodzi przed resztą."],
  sciezka: ["Ścieżką kursu", "Od pierwszego zestawu do ostatniego, bez wyjątku dla lekcji."],
  "od-latwych": ["Od najprostszych", "Najpierw A1, potem A2 — niezależnie od zestawu."],
  nadrabianie: ["Nadrabianie", "Najsłabsze zaległości przodem, nowe rzadko."],
  spokojny: ["Spokojny dzień", "Krótka sesja, i dłużej uczy, zanim zacznie sprawdzać."],
  intensywnie: ["Intensywnie", "Duże sesje, ostrzejsza bramka, produkcja od pierwszego razu."],
  wlasny: ["Po swojemu", "Twoje własne ustawienia."],
};

const ORDER_SAID = {
  lesson: "świeża lekcja, potem ścieżka kursu",
  course: "ścieżka kursu",
  axis: "po poziomie",
  plan: "według mojego planu",
  shuffle: "wymieszane",
};

const DEBT_SAID = {
  overdue: "najbardziej zaległe",
  weakest: "najsłabsze najpierw",
  course: "po kolei, jak w kursie",
  plan: "według mojego planu",
};

let data = null;
// What is on screen but not yet saved, so hovering a mode can show its
// consequences without committing to it -- the same gesture `settings.js` uses
// for themes.
let draft = null;
let previewing = null;

link.addEventListener("click", () => show("howstudy"));

document.addEventListener("repetita:view", (e) => {
  if (e.detail?.view === "howstudy") load();
});

// The chip in the header, refreshed from every screen. A queue built to
// somebody's settings should say so from wherever they are, not only on the page
// that sets it.
export async function refreshChip() {
  try {
    const body = await api("/api/style");
    const [name] = SAID[body.style.mode] || [body.style.mode];
    const plain = body.style.mode === "kurs";
    link.textContent = plain ? "" : `uczę się: ${name.toLowerCase()}`;
    link.hidden = plain;
  } catch {
    link.hidden = true;
  }
}

async function load() {
  try {
    data = await api("/api/style");
    draft = { ...data.style };
    render();
    refreshPreview();
  } catch (error) {
    fill(panel, el("p", { class: "muted", text: `nie udało się wczytać (${error.message})` }));
  }
}

// --- the modes ------------------------------------------------------------

function modeRow(mode) {
  const [name, why] = SAID[mode.key] || [mode.key, ""];
  const on = draft.mode === mode.key;
  const { key, ...recipe } = mode;
  return el("li", {}, [
    el(
      "button",
      {
        type: "button",
        class: `how-pick${on ? " on" : ""}`,
        // Preview on hover, not only on click: you should be able to see what a
        // mode would do to tomorrow before choosing it.
        onmouseenter: () => peek(recipe),
        onfocus: () => peek(recipe),
        onclick: () => {
          draft = { ...recipe, mode: mode.key };
          render();
          refreshPreview();
        },
      },
      [
        el("span", { class: "how-name", text: name }),
        el("span", { class: "how-why muted", text: why }),
      ],
    ),
  ]);
}

function peek(recipe) {
  clearTimeout(previewing);
  previewing = setTimeout(() => refreshPreview(recipe), 120);
}

// --- the recipe, spelled out ---------------------------------------------

function picker(label, value, options, said, onchange) {
  return el("label", { class: "how-line" }, [
    el("span", { class: "how-label", text: label }),
    el(
      "select",
      {
        onchange: (e) => {
          onchange(e.target.value);
          render();
          refreshPreview();
        },
      },
      options.map((o) =>
        el("option", { value: o, text: said[o] || o, selected: o === value ? "selected" : null }),
      ),
    ),
  ]);
}

function knobRow(knob) {
  const value = draft.knobs[knob.key] ?? knob.fallback;
  return el("div", { class: "how-knob" }, [
    el("input", {
      type: "range",
      min: knob.min,
      max: knob.max,
      step: knob.step,
      value,
      // On `change`, not `input`: dragging would otherwise write a revision per
      // frame, and revisions are the record of what was tried.
      onchange: (e) => {
        draft.knobs[knob.key] = Number(e.target.value);
        draft.mode = "wlasny";
        render();
        refreshPreview();
      },
    }),
    el("span", { class: "how-says", text: knob.says(value) }),
  ]);
}

function switchRow(sw) {
  const on = draft.knobs[sw.key] ?? sw.fallback;
  return el("label", { class: "how-knob how-switch" }, [
    el("input", {
      type: "checkbox",
      checked: on ? "checked" : null,
      onchange: (e) => {
        draft.knobs[sw.key] = e.target.checked;
        draft.mode = "wlasny";
        render();
        refreshPreview();
      },
    }),
    el("span", { class: "how-says", text: sw.says(on) }),
  ]);
}

function recipePane() {
  const orderings = data.orderings.filter((o) => o !== "axis" || data.axes.length);
  return el("details", { class: "how-details" }, [
    el("summary", { text: "Pokaż wszystko" }),
    picker("Nowe biorę", draft.introductions, orderings, ORDER_SAID, (v) => {
      draft.introductions = v;
      if (v === "axis" && !draft.intro_axis) draft.intro_axis = data.axes[0] || "";
      draft.mode = "wlasny";
    }),
    draft.introductions === "axis" && data.axes.length > 1
      ? picker("Po osi", draft.intro_axis, data.axes, {}, (v) => {
          draft.intro_axis = v;
          draft.mode = "wlasny";
        })
      : null,
    picker("Powtórki w kolejności", draft.debt, data.debt_orderings, DEBT_SAID, (v) => {
      draft.debt = v;
      draft.mode = "wlasny";
    }),
    ...KNOBS.map(knobRow),
    ...SWITCHES.map(switchRow),
    el("p", {
      class: "muted how-note",
      // Said on the screen rather than only in an ADR, because it is the thing a
      // learner most needs to be able to trust about this page.
      text:
        "Żadne z tych ustawień nie zmienia tego, ile masz zaległych — tylko to, " +
        "w jakiej kolejności je spotykasz i jak szybko dochodzi nowy materiał.",
    }),
  ]);
}

// --- what it would do -----------------------------------------------------

async function refreshPreview(recipe = null) {
  const want = recipe || draft;
  try {
    const out = await api("/api/style/preview", {
      method: "POST",
      body: JSON.stringify({ ...want, budget: want.knobs?.batch ?? 20 }),
    });
    const target = panel.querySelector(".how-preview");
    if (target) fill(target, ...previewBody(out));
  } catch (error) {
    const target = panel.querySelector(".how-preview");
    if (target) fill(target, el("p", { class: "muted", text: `podgląd: ${error.message}` }));
  }
}

function previewBody(out) {
  const units = Object.entries(out.by_unit).sort((a, b) => b[1] - a[1]);
  const most = units.length ? units[0][1] : 1;
  return [
    el("h3", { text: "Co jutro" }),
    el("p", { class: "muted", text: `${out.picked} kart, z ${units.length} zestawów.` }),
    el(
      "ul",
      { class: "how-bars" },
      units.slice(0, 8).map(([unit, n]) =>
        el("li", { class: "bar-row" }, [
          el("span", { class: "bar-name", text: unit }),
          el("span", { class: "bar-track" }, [
            el("span", { class: "bar-fill", style: `width:${(n / most) * 100}%` }),
          ]),
          el("span", { class: "bar-n muted", text: String(n) }),
        ]),
      ),
    ),
    out.names.length
      ? el("p", { class: "muted how-sample", text: `np. ${out.names.slice(0, 8).join(", ")}` })
      : null,
    out.plan_missing
      ? el("p", { class: "warn", text: "Plan, na który to wskazuje, już nie istnieje." })
      : null,
    forecastBlock(),
  ];
}

// The 14-day debt curve. `daily.forecast()` has computed this since it was
// written and nothing has ever drawn it.
function forecastBlock() {
  const f = data.forecast || [];
  if (!f.length) return null;
  const top = Math.max(...f, 1);
  return el("div", { class: "how-forecast" }, [
    el("h3", { text: "Za dwa tygodnie" }),
    el(
      "ul",
      { class: "spark" },
      f.map((n, i) =>
        el("li", {
          class: "spark-bar",
          style: `height:${(n / top) * 100}%`,
          title: `za ${i} dni: ${n} zaległych`,
        }),
      ),
    ),
    el("p", {
      class: "muted",
      text: `Dziś ${f[0]} zaległych, za 14 dni około ${f[f.length - 1]}.`,
    }),
  ]);
}

// --- the page -------------------------------------------------------------

function render() {
  const [name] = SAID[draft.mode] || [draft.mode];
  fill(
    panel,
    el("div", { class: "how-grid" }, [
      el("section", { class: "pane" }, [
        el("h2", { text: "Jak się uczę" }),
        el("p", { class: "muted", text: `Teraz: ${name}.` }),
        el("ul", { class: "how-modes" }, data.modes.map(modeRow)),
        recipePane(),
        el("div", { class: "how-actions" }, [
          el("button", {
            class: "quiet",
            type: "button",
            text: "Anuluj",
            onclick: () => {
              draft = { ...data.style };
              render();
              refreshPreview();
            },
          }),
          el("button", { class: "primary", type: "button", text: "Zapisz", onclick: save }),
        ]),
      ]),
      el("section", { class: "pane how-preview" }),
    ]),
  );
}

async function save() {
  try {
    const out = await api("/api/style", { method: "PUT", body: JSON.stringify(draft) });
    data.style = out.style;
    draft = { ...out.style };
    render();
    refreshPreview();
    refreshChip();
    // The session the learner is in was built under the old settings, so it is
    // told rather than left to notice.
    document.dispatchEvent(new CustomEvent("repetita:restudy", { detail: { plan: null } }));
    toast("Zapisane.");
  } catch (error) {
    toast(`nie zapisano (${error.message})`, { tone: "warn" });
  }
}

refreshChip();
