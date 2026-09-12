// The course picker: a flag in the corner, and what happens when it changes.
//
// One app, several courses, one at a time. Switching is a page reload rather
// than a re-render of five panels: every module holds material for the course
// it loaded with -- the session queue, the Manage board, the Create editor, the
// handles behind every open question -- and reloading is both the honest way to
// replace all of it and a line of code instead of five teardown paths.
//
// The choice is remembered twice on purpose. `localStorage` is read before the
// first request, so the right course is served from the very first call rather
// than flashing the default; the server is told as well, so a cleared cache or
// a second browser still opens where the last session left off.

import { api, currentCourse, setCourse, url } from "./api.js";
import { el, fill, toast } from "./dom.js";

let courses = [];
let open = false;
// Whether this account has enrolled in anything at all. When it has not, every
// course is "mine" and the second section of the list is empty — which is the
// single-account case and every database that predates accounts.
let enrolling = false;

const mount = document.getElementById("course-pick");

export async function start() {
  if (!mount) return;
  try {
    const body = await api("/api/courses");
    courses = body.courses || [];
    enrolling = Boolean(body.enrolling);
    // The server has the last word on which course is being served: it knows
    // what the database holds, and a stale localStorage entry naming a course
    // that has been renamed would otherwise leave the flag lying.
    if (body.selected && body.selected !== currentCourse()) setCourse(body.selected);
  } catch {
    // A picker that cannot load must not take the app down with it: one course
    // is the ordinary case and the app works perfectly without this control.
    return;
  }
  render();
}

function current() {
  return courses.find((c) => c.id === currentCourse()) || courses[0];
}

function mine() {
  // The course you are looking at counts as yours whether or not you have
  // joined it. Otherwise the flag in the corner shows a course the list files
  // under "other", which reads as a mistake — and it is reachable honestly: a
  // remembered course, a link, a course you left without switching away.
  return courses.filter((c) => c.enrolled || c.id === currentCourse());
}

function render() {
  if (!mount) return;
  const now = current();
  if (!now || courses.length < 2) {
    // One course needs no picker. Showing a control that cannot do anything
    // invites the question of what it is for.
    fill(mount);
    return;
  }

  fill(
    mount,
    el("button", {
      class: `flagpick${open ? " on" : ""}`,
      type: "button",
      title: `${said(now)} — click to switch course`,
      "aria-haspopup": "listbox",
      "aria-expanded": open ? "true" : "false",
      onclick: (event) => {
        event.stopPropagation();
        open = !open;
        render();
      },
    }, [
      el("span", { class: "flagpick-flag", text: now.flag }),
      el("span", { class: "flagpick-caret", text: "▾" }),
    ]),
    open ? list() : null,
  );
}

function list() {
  const up = new Set(mine().map((c) => c.id));
  const others = courses.filter((c) => !up.has(c.id));
  return el(
    "ul",
    { class: "flaglist", role: "listbox" },
    [
      ...mine().map(row),
      // The rest, offered rather than hidden. A course you cannot see is a
      // course you cannot join, and absence from `enrolments` was never meant
      // to mean "cannot see it".
      others.length
        ? el("li", { class: "flaglist-head", text: "Inne kursy" })
        : null,
      ...others.map(row),
    ].filter(Boolean),
  );
}

function row(c) {
  return el("li", {}, [
    el(
      "button",
      {
        class: `flaglist-row${c.id === currentCourse() ? " on" : ""}`,
        type: "button",
        role: "option",
        "aria-selected": c.id === currentCourse() ? "true" : "false",
        onclick: () => choose(c),
      },
      [
        el("span", { class: "flagpick-flag", text: c.flag }),
        el("span", { class: "flaglist-name", text: said(c) }),
        // What is waiting there. The number is the reason to switch, so it
        // belongs on the thing you click rather than behind it. A course you
        // have not joined has no queue of yours, so it shows its size instead.
        el("span", {
          class: "flaglist-owed",
          text: c.owed ? `${c.owed} owed` : `${c.notes}`,
          title: c.owed ? `${c.owed} waiting` : `${c.notes} exercises`,
        }),
      ],
    ),
  ]);
}

function said(course) {
  const title = course.title || {};
  return title.pl || title.en || course.id;
}

async function choose(course) {
  open = false;
  // Opening a course you are not enrolled in enrols you. Clicking it is the
  // statement; a second confirming step would be asking twice for one decision.
  if (enrolling && !course.enrolled) {
    try {
      await fetch(url(`/api/courses/${encodeURIComponent(course.id)}/join`), { method: "POST" });
    } catch {
      // The switch still happens. The enrolment catches up next time.
    }
  }
  if (course.id === currentCourse()) {
    render();
    return;
  }
  setCourse(course.id);
  try {
    await fetch(url(`/api/courses/${encodeURIComponent(course.id)}/select`), { method: "POST" });
  } catch {
    // The browser remembers even when the server could not be told, so the
    // switch still happens. The two agree again on the next successful call.
  }
  // Everything on screen belongs to the course being left.
  window.location.reload();
}

// Clicking anywhere else closes it, which is what every menu does and what a
// person will try first.
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

start().catch(() => toast("Could not read the course list", { tone: "bad" }));
