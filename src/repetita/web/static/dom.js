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

function asText(value) {
  return Array.isArray(value) ? value.join(" · ") : String(value);
}

// The question, then whatever else the server judged safe to show alongside it.
// `card.fields` is already filtered server-side; nothing here decides visibility.
export function question(card) {
  const asked = card.ask.map((name) =>
    el("p", { class: "ask", text: asText(card.fields[name]) }),
  );
  const rest = Object.entries(card.fields)
    .filter(([name]) => !card.ask.includes(name))
    .map(([name, value]) =>
      el("p", { class: "aside" }, [
        el("span", { class: "label", text: name }),
        el("span", { text: asText(value) }),
      ]),
    );
  return el("div", { class: "question" }, [...asked, ...rest]);
}
