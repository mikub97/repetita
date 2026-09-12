// How the app looks, and where that choice is kept.
//
// Three skins, named rather than numbered: a stored `2` would mean nothing the
// day a fourth is added in the middle. Each is only a set of tokens on
// `<body data-theme>` — the same mechanism `data-tab` has always used for
// `--shell`. No theme changes what a screen does, and none of them has its own
// markup.
//
// The choice is remembered twice, for the reason the course picker is: the
// browser's copy is what paints the right theme on the *first frame*, and the
// database's is what survives a cleared cache or answers a second browser.

import { api } from "./api.js";
import { el, fill, toast } from "./dom.js";

const KEY = "repetita-theme";
const DEFAULT = "spokojny";

const SAID = {
  spokojny: ["Spokojny", "Jak dotąd — cicho i zwięźle."],
  duzy: ["Duży", "Większe litery, większe przyciski, karta jak fiszka."],
  cieply: ["Ciepły", "Ciepłe barwy, zaokrąglenia i drobna animacja przy dobrej odpowiedzi."],
};

let open = false;
let chosen = DEFAULT;
let saved = DEFAULT;

const mount = document.getElementById("settings");

// Applied before anything renders. Reading it here rather than after a fetch is
// the whole reason the browser keeps a copy: a theme that arrives with the
// first response arrives one repaint too late, and the default flashes.
function remembered() {
  try {
    return localStorage.getItem(KEY) || DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function apply(theme) {
  // The default declares no tokens of its own, so it is the absence of the
  // attribute rather than a value of it.
  if (theme && theme !== DEFAULT) document.body.dataset.theme = theme;
  else delete document.body.dataset.theme;
}

chosen = saved = remembered();
apply(chosen);

function render() {
  if (!mount) return;
  fill(
    mount,
    el("button", {
      class: `gear${open ? " on" : ""}`,
      type: "button",
      title: "How the app looks",
      "aria-label": "Settings",
      "aria-expanded": open ? "true" : "false",
      onclick: (event) => {
        event.stopPropagation();
        open = !open;
        if (!open) revert();
        render();
      },
    }, [el("span", { class: "gear-glyph", text: "⚙" })]),
    open ? panel() : null,
  );
}

function panel() {
  return el("div", {
    class: "settings",
    // Clicks inside the panel must not reach the document handler below, which
    // closes it and reverts. Choosing a theme *is* a click inside the panel, so
    // without this the picker cancelled itself the moment it was used.
    onclick: (event) => event.stopPropagation(),
  }, [
    el("h3", { class: "settings-head", text: "Wygląd" }),
    el(
      "ul",
      { class: "settings-list" },
      Object.entries(SAID).map(([name, [title, why]]) =>
        el("li", {}, [
          el("button", {
            class: `settings-pick${name === chosen ? " on" : ""}`,
            type: "button",
            // Applied on hover as well as on click, so the list is a preview
            // rather than a description of one. Leaving without saving puts
            // back whatever was saved.
            onmouseenter: () => apply(name),
            onfocus: () => apply(name),
            onclick: () => {
              chosen = name;
              apply(name);
              render();
            },
          }, [
            el("span", { class: "settings-name", text: title }),
            el("span", { class: "settings-why muted", text: why }),
          ]),
        ]),
      ),
    ),
    el("div", { class: "settings-row" }, [
      el("span", { class: "muted settings-hint", text: "Podgląd działa od razu." }),
      el("button", { class: "quiet", type: "button", text: "Anuluj", onclick: () => {
        open = false;
        revert();
        render();
      } }),
      el("button", { class: "primary", type: "button", text: "Zapisz", onclick: save }),
    ]),
  ]);
}

function revert() {
  chosen = saved;
  apply(saved);
}

async function save() {
  const theme = chosen;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* private mode -- the server copy below still remembers */
  }
  saved = theme;
  open = false;
  render();
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ theme }) });
  } catch (error) {
    // The look already changed and the browser already remembers, so this is
    // not a failure the person needs to undo -- it is one they should know
    // about, because another browser will not see it.
    toast(`Zapisano tylko w tej przeglądarce — ${error.message}`, { tone: "warn" });
  }
}

// The server has the last word on start-up, for the same reason the course
// picker asks: it is what a second browser, or this one after a cleared cache,
// has to learn from.
api("/api/settings")
  .then((body) => {
    if (body.theme && body.theme !== chosen) {
      chosen = saved = body.theme;
      apply(body.theme);
      try {
        localStorage.setItem(KEY, body.theme);
      } catch {
        /* nothing to remember it in */
      }
    }
    render();
  })
  .catch(render);

document.addEventListener("click", () => {
  if (!open) return;
  open = false;
  revert();
  render();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && open) {
    open = false;
    revert();
    render();
  }
});
