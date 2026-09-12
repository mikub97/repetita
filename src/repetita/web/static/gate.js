// The sign-in page, which is the whole of what it does.
//
// Separate from `app.js` on purpose: this page loads before there is anybody to
// load material for, so it shares nothing with the application behind it beyond
// the stylesheet and the mount prefix.

import { url } from "./api.js";

const form = document.getElementById("gate-form");
const said = document.getElementById("gate-said");
const pass = document.getElementById("gate-pass");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  said.hidden = true;
  const body = {
    name: document.getElementById("gate-name").value,
    password: pass.value,
  };
  let answer;
  try {
    answer = await fetch(url("/api/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    said.textContent = "Nie udało się połączyć";
    said.hidden = false;
    return;
  }
  if (!answer.ok) {
    // One message for every kind of no, because the server gives one answer for
    // every kind of no: which of "no such account", "wrong password" and
    // "deactivated" it was is not something this page should be able to tell.
    said.textContent = "Nie to hasło";
    said.hidden = false;
    pass.value = "";
    pass.focus();
    return;
  }
  window.location.href = document.body.dataset.base || "/";
});
