// Who you are, and how to become somebody else.
//
// A chip in the bar beside the gear, because both are about the app rather
// than about the material — the flag on the other side scopes what you are
// looking at, this scopes whose it is.
//
// Switching is a page reload, for the same reason switching course is: every
// module on this page holds one person's material and one person's queue, and
// reloading is the honest way to replace all of it rather than five teardown
// paths that each forget something.
//
// Absent entirely where there is no login — a mounted repetita, or a database
// where nobody has a password yet. A control that cannot do anything invites
// the question of what it is for.

import { api, url } from "./api.js";
import { el, fill, toast } from "./dom.js";

let me = null;
let others = [];
let open = false;

const mount = document.getElementById("who");

export async function start() {
  if (!mount) return;
  try {
    const body = await api("/api/me");
    if (!body.login) return; // nobody signs in here; show nothing at all
    me = body.user;
    others = body.accounts || [];
  } catch {
    // The chip must not take the app down with it. Somebody who cannot see who
    // they are can still study as whoever they are.
    return;
  }
  render();
}

function initials(who) {
  return (who.display || who.name || "?").trim().slice(0, 2).toUpperCase();
}

function render() {
  if (!mount || !me) return;
  fill(
    mount,
    el(
      "button",
      {
        class: `who${open ? " on" : ""}`,
        type: "button",
        title: `${me.display} — kliknij, żeby się przełączyć`,
        "aria-haspopup": "menu",
        "aria-expanded": open ? "true" : "false",
        onclick: (event) => {
          event.stopPropagation();
          open = !open;
          render();
        },
      },
      [el("span", { class: "who-face", text: initials(me) })],
    ),
    open ? menu() : null,
  );
}

function menu() {
  return el(
    "div",
    {
      class: "who-menu",
      role: "menu",
      // Clicks inside the menu are not clicks outside it. Without this they
      // bubble to the document handler that closes it, and the panel dismisses
      // itself the moment you touch it.
      onclick: (event) => event.stopPropagation(),
    },
    [
      el("p", { class: "who-now", text: me.display }),
      ...others.map((other) =>
        el("button", {
          class: "who-row",
          type: "button",
          role: "menuitem",
          text: other.display,
          onclick: () => ask(other),
        }),
      ),
      el("button", {
        class: "who-out",
        type: "button",
        role: "menuitem",
        text: "Wyloguj",
        onclick: leave,
      }),
    ],
  );
}

// Switching asks for the password. Not ceremony: these four accounts live on
// one machine, and an account you can enter by picking it from a list is a
// label rather than an account.
function ask(other) {
  open = false;
  render();
  const said = window.prompt(`Hasło dla: ${other.display}`);
  if (said === null) return;
  enter(other.name, said);
}

async function enter(name, password) {
  try {
    const answer = await fetch(url("/api/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, password }),
    });
    if (!answer.ok) {
      toast("Nie to hasło", { tone: "bad" });
      return;
    }
  } catch {
    toast("Nie udało się przełączyć", { tone: "bad" });
    return;
  }
  // Everything on screen belongs to the person being left.
  window.location.reload();
}

async function leave() {
  try {
    await fetch(url("/api/logout"), { method: "POST" });
  } catch {
    // Reloading lands on the login page either way: the guard reads the
    // session, not this call's result.
  }
  window.location.reload();
}

document.addEventListener("click", () => {
  if (!open) return;
  open = false;
  render();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && open) {
    open = false;
    render();
  }
});

start().catch(() => {});
