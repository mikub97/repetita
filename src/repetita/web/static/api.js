// Talking to the server, and not losing an answer when that fails.
//
// The load-bearing distinction is between a network that is not there and a
// server that said no. They are the same `catch` in naive code and they need
// opposite handling: an unreachable server means keep the answer and try later,
// a 400 means the answer was rejected and retrying it forever is a bug.

const PENDING = "repetita-pending";

// Where the app is mounted, from the server, which is the only thing that knows.
// Standalone this is "/"; inside a host it is whatever prefix the host chose.
// Hard-coding root-relative paths works right up until someone mounts the app,
// and then every call 404s at once.
const BASE = (document.body.dataset.base || "/").replace(/\/$/, "");

// Which course every call is about. Held here and put on every URL, for the
// same reason BASE is: it is true of every request, and a call that forgets it
// does not fail -- it quietly answers about a different course.
const COURSE = "repetita-course";
let course = null;

try {
  course = localStorage.getItem(COURSE);
} catch {
  // Private mode, storage disabled. The server's remembered course answers.
}

export function currentCourse() {
  return course;
}

export function setCourse(id) {
  course = id || null;
  try {
    if (course) localStorage.setItem(COURSE, course);
    else localStorage.removeItem(COURSE);
  } catch {
    /* nothing to remember it in -- the server still knows */
  }
}

export function url(path) {
  if (!course) return BASE + path;
  // The path may already carry a query (`/api/session?plan=3`).
  return `${BASE}${path}${path.includes("?") ? "&" : "?"}course=${encodeURIComponent(course)}`;
}

export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(url(path), {
      headers: { "content-type": "application/json" },
      ...options,
    });
  } catch (cause) {
    // fetch rejects only when the request never reached a server.
    const error = new Error("offline");
    error.offline = true;
    error.cause = cause;
    throw error;
  }

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || String(response.status));
    error.status = response.status;
    throw error;
  }
  return body;
}

export function readPending() {
  try {
    return JSON.parse(localStorage.getItem(PENDING) || "[]");
  } catch {
    // Corrupt or unavailable storage must not take the app down with it.
    return [];
  }
}

function writePending(items) {
  try {
    localStorage.setItem(PENDING, JSON.stringify(items));
  } catch {
    /* private mode, quota, storage disabled -- nothing to do but carry on */
  }
}

export function queueAnswer(body) {
  writePending([...readPending(), { body, at: Date.now() }]);
  return readPending().length;
}

let flushing = false;

export async function flushPending() {
  if (flushing) return { sent: 0, left: readPending().length, rejected: 0 };
  flushing = true;

  // Claim the whole queue up front and clear it. Leaving items in place while
  // sending them is how one gets sent twice: a second flush, triggered by an
  // `online` event mid-flight, would read the same rows.
  const claimed = readPending();
  writePending([]);
  const left = [];
  let sent = 0;
  let rejected = 0;

  try {
    for (const item of claimed) {
      try {
        await api("/api/answer", { method: "POST", body: JSON.stringify(item.body) });
        sent += 1;
      } catch (error) {
        if (error.offline) {
          left.push(item);
        } else {
          // The server answered and refused. Retrying forever would never
          // succeed, so the answer is dropped -- but counted, because an answer
          // disappearing without a word is the thing this module exists against.
          rejected += 1;
        }
      }
    }
  } finally {
    // Anything queued while this was running goes after what is left over, in
    // the order it was given.
    writePending(left.concat(readPending()));
  }

  flushing = false;
  return { sent, left: left.length, rejected };
}
