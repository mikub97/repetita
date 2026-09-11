// Tiny DOM helpers shared by the form modules.
//
// Everything is built with createElement and textContent rather than innerHTML:
// course content is arbitrary text from YAML files, and a form module is not the
// place to be deciding what is safe to interpolate into markup.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

// `node.append(x)` stringifies anything that is not a Node, so a conditional
// child written as `cond ? el(...) : null` puts the literal text "null" on the
// page. `el()` already filters its children; this is the same guarantee for the
// places that append to an existing node.
export function fill(node, ...children) {
  node.replaceChildren();
  for (const child of children.flat()) {
    if (child) node.append(child);
  }
  return node;
}

function asText(value) {
  return Array.isArray(value) ? value.join(" · ") : String(value);
}

// The question, then whatever else the server judged safe to show alongside it.
// `card.fields` is already filtered server-side; nothing here decides visibility.
//
// Field names are not printed. A learner reading "cue: morar — imperfeito, eu"
// is being shown the database's word for a hint, which tells them nothing they
// needed and quite a lot they did not. The role is carried by how it looks
// instead: the question large, the hint under it, everything else quieter.
export function question(card) {
  const asked = card.ask.map((name, i) =>
    el("p", { class: i === 0 ? "ask" : "hint", text: asText(card.fields[name]) }),
  );
  const rest = Object.entries(card.fields)
    .filter(([name]) => !card.ask.includes(name))
    .map(([, value]) => el("p", { class: "aside", text: asText(value) }));
  return el("div", { class: "question" }, [...asked, ...rest]);
}


// How well something is known, drawn the same way everywhere.
//
// A dot for the glance and a bar for the composition. Both come from the one
// definition in `core/mastery.py`, so the Study tab, the Design tab's topics and
// the Manage board's rows cannot drift into disagreeing about the same cards.
export function dot(state, title) {
  return el("span", { class: `dot ${state || "untouched"}`, title: title || "" });
}

export function masteryBar(m) {
  if (!m || !m.total) return el("div", { class: "mastery" });
  const pct = (n) => `${(n / m.total) * 100}%`;
  return el("div", {
    class: "mastery",
    // Spelled out because the bar is four shades of the same idea, and the
    // hatched segment in particular needs saying rather than decoding.
    title:
      `${m.total} cards — ${m.earned} learned, ${m.declared} marked known, ` +
      `${m.working} in progress, ${m.untouched} not started`,
  }, [
    m.earned ? el("span", { class: "m-earned", style: `width:${pct(m.earned)}` }) : null,
    m.declared ? el("span", { class: "m-declared", style: `width:${pct(m.declared)}` }) : null,
    m.working ? el("span", { class: "m-working", style: `width:${pct(m.working)}` }) : null,
  ]);
}
