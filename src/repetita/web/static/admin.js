// The admin page: every table in the database, and the ones it will not touch.
//
// A fifth tab, present only for an account marked admin — the button ships in
// the markup hidden, because `designer.js` reads every tab by id and a button
// that is sometimes absent is a null dereference waiting for the wrong account
// to sign in.
//
// Generic over the schema on purpose: 25 tables, and a screen each would be 25
// screens that drift. What is not generic is which tables may be written to, and
// that decision lives in `store/browse.py` rather than here — a UI that decides
// what is safe to edit is a UI that can be bypassed with `curl`.

import { api, url } from "./api.js";
import { el, fill, toast } from "./dom.js";
import { show } from "./designer.js";

const panel = document.getElementById("admin");
const tab = document.getElementById("tab-admin");

// Why a table is read-only, in words. The server sends a code and the table's
// name; the prose is here because UI text is translated and the engine ships
// none of it (CLAUDE.md). The same sentence appears in the banner over the
// table and in the toast when a write is refused, which is the point of having
// one place for it.
const WHY = {
  review_log: "każda odpowiedź, jakiej kiedykolwiek udzieliłeś. Żaden eksport tego nie przywróci.",
  card_state: "gdzie jesteś z każdą kartą. Żaden eksport tego nie przywróci.",
  plan_revisions: "jak wyglądał plan, kiedy padła pod nim odpowiedź.",
};

const OWNED = {
  manage: "zakładka Manage",
};

function why(table) {
  if (table.frozen === "irreplaceable") {
    return WHY[table.name] || "tych wierszy nie da się odtworzyć.";
  }
  // A write here owes four things -- edited_at, content_hash, re-expansion,
  // reclassify -- and the screen that owes them is the one to use.
  const go = OWNED[table.instead];
  return go
    ? `zapis tutaj pominąłby to, co przy zmianie materiału trzeba zrobić. Zmień to w: ${go}.`
    : "te wiersze są odtwarzane automatycznie i same z siebie nic nie znaczą.";
}

let tables = [];
let here = null; // the table being looked at
let page = null; // its current page of rows
let filters = {};

tab?.addEventListener("click", () => show("admin"));

document.addEventListener("repetita:view", (e) => {
  if (e.detail.view === "admin") start();
});

// Whether this account gets the tab at all. Asked once, at load, so the tab is
// there before anybody looks for it.
(async () => {
  if (!tab) return;
  try {
    const me = await api("/api/me");
    if (me.user?.admin) tab.hidden = false;
  } catch {
    // No tab. An account that cannot tell whether it is an admin is not one.
  }
})();

async function start() {
  if (!panel) return;
  fill(panel, el("p", { class: "muted", text: "Ładowanie…" }));
  try {
    tables = (await api("/api/admin/tables")).tables || [];
  } catch {
    fill(panel, el("p", { class: "muted", text: "Nie udało się wczytać tabel." }));
    return;
  }
  render();
}

function render() {
  if (!panel) return;
  fill(
    panel,
    el("div", { class: "admin-wrap" }, [
      el("nav", { class: "admin-list", "aria-label": "Tabele" }, tables.map(listRow)),
      el("div", { class: "admin-main" }, here ? rows() : blurb()),
    ]),
  );
}

function blurb() {
  return el("div", { class: "admin-blurb" }, [
    el("p", { text: "Wybierz tabelę po lewej." }),
    el("p", {
      class: "muted",
      text:
        "Historia nauki (review_log, card_state) jest tylko do odczytu — tych " +
        "wierszy nie da się odtworzyć z żadnego eksportu. Materiał zmienia się " +
        "w zakładce Manage, która wie, co przy tym trzeba zrobić.",
    }),
  ]);
}

function listRow(t) {
  return el(
    "button",
    {
      class: `admin-tab${here === t.name ? " on" : ""}${t.editable ? "" : " frozen"}`,
      type: "button",
      title: t.editable ? `${t.rows} wierszy` : why(t),
      onclick: () => open(t.name),
    },
    [
      el("span", { class: "admin-tab-name", text: t.name }),
      el("span", { class: "admin-tab-n", text: String(t.rows) }),
      // A padlock rather than hiding the table. A read-only table you cannot
      // see is a table you go looking for in `sqlite3`.
      t.editable ? null : el("span", { class: "admin-tab-lock", text: "🔒" }),
    ],
  );
}

async function open(name, offset = 0) {
  here = name;
  filters = {};
  await load(offset);
}

async function load(offset = 0) {
  const query = Object.entries(filters)
    .map(([k, v]) => `&f.${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("");
  try {
    page = await api(`/api/admin/tables/${encodeURIComponent(here)}?offset=${offset}${query}`);
  } catch {
    toast("Nie udało się wczytać wierszy", { tone: "bad" });
    return;
  }
  render();
}

function rows() {
  const t = page?.table;
  if (!t) return el("p", { class: "muted", text: "…" });
  return el("div", {}, [
    el("h2", { class: "admin-head", text: t.name }),
    t.editable
      ? null
      : el("p", { class: "admin-frozen" }, [
          el("b", { text: "Tylko do odczytu. " }),
          el("span", { text: why(t) }),
        ]),
    el("p", { class: "muted admin-count", text: `${page.total} wierszy` }),
    el("div", { class: "admin-scroll" }, [table(t)]),
    pager(),
  ]);
}

function table(t) {
  return el("table", { class: "admin-table" }, [
    el("thead", {}, [
      el("tr", {}, [
        ...t.columns.map((c) =>
          el("th", {
            text: c,
            class: t.primary_key.includes(c) ? "admin-pk" : "",
            title: t.primary_key.includes(c) ? "klucz główny" : "",
          }),
        ),
        t.editable ? el("th", { text: "" }) : null,
      ]),
    ]),
    el("tbody", {}, page.rows.map((r) => row(t, r))),
  ]);
}

// A row inside an editable table that is not editable: the session key, which
// is shown as dots so that its existence is visible and its value is not.
function locked(t, r) {
  const rule = t.locked;
  return Boolean(rule && rule.values.includes(String(r[rule.column])));
}

function row(t, r) {
  const held = locked(t, r);
  return el("tr", { class: held ? "admin-held" : "" }, [
    ...t.columns.map((c) =>
      el(
        "td",
        {
          class: t.primary_key.includes(c) ? "admin-pk" : "",
          // Editable in place, except the primary key: changing one is deleting
          // a row and writing another, and whatever pointed at the old one
          // would not follow.
          contenteditable: t.editable && !held && !t.primary_key.includes(c) ? "true" : null,
          onblur: (event) => save(t, r, c, event.target.textContent),
        },
        [el("span", { text: cell(r[c]) })],
      ),
    ),
    t.editable
      ? el("td", {}, [
          held
            ? el("span", { class: "muted", text: "—", title: "Tego wiersza się nie zmienia" })
            : el("button", {
                class: "admin-del",
                type: "button",
                text: "Usuń",
                title: "Usuń ten wiersz",
                onclick: () => remove(t, r),
              }),
        ])
      : null,
  ]);
}

// A refusal from `browse.py` arrives as `<code>:<table>`; anything else is
// already a sentence (a SQLite constraint, say) and is shown as it came.
function said(error) {
  const [code, name] = String(error || "").split(":");
  if (!name || !(code === "irreplaceable" || code === "owned_elsewhere")) return error;
  return `${name}: ${why({ frozen: code, name, instead: tables.find((t) => t.name === name)?.instead })}`;
}

function cell(value) {
  if (value === null || value === undefined) return "";
  return String(value);
}

function keyOf(t, r) {
  return Object.fromEntries(t.primary_key.map((c) => [c, r[c]]));
}

async function save(t, r, column, text) {
  const was = cell(r[column]);
  if (text === was) return;
  try {
    const answer = await fetch(url(`/api/admin/tables/${encodeURIComponent(t.name)}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: keyOf(t, r), values: { [column]: text } }),
    });
    if (!answer.ok) {
      const body = await answer.json().catch(() => ({}));
      toast(said(body.error) || "Nie zapisano", { tone: "bad" });
      await load(page.offset);
      return;
    }
  } catch {
    toast("Nie zapisano", { tone: "bad" });
    return;
  }
  r[column] = text;
  toast(`${t.name}.${column} zapisane`);
}

async function remove(t, r) {
  // One confirm, and it names the row. Deleting from a table browser is the
  // one action here with nothing behind it.
  const what = t.primary_key.map((c) => `${c}=${r[c]}`).join(", ");
  if (!window.confirm(`Usunąć wiersz ${what} z ${t.name}?`)) return;
  try {
    const answer = await fetch(url(`/api/admin/tables/${encodeURIComponent(t.name)}`), {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: keyOf(t, r) }),
    });
    if (!answer.ok) {
      const body = await answer.json().catch(() => ({}));
      toast(said(body.error) || "Nie usunięto", { tone: "bad" });
      return;
    }
  } catch {
    toast("Nie usunięto", { tone: "bad" });
    return;
  }
  await load(page.offset);
}

function pager() {
  const size = page.rows.length;
  const at = page.offset;
  if (page.total <= size && at === 0) return null;
  return el("div", { class: "admin-pager" }, [
    el("button", {
      type: "button",
      text: "‹",
      disabled: at <= 0 ? "true" : null,
      onclick: () => load(Math.max(0, at - 50)),
    }),
    el("span", { class: "muted", text: `${at + 1}–${at + size} z ${page.total}` }),
    el("button", {
      type: "button",
      text: "›",
      disabled: at + size >= page.total ? "true" : null,
      onclick: () => load(at + 50),
    }),
  ]);
}
