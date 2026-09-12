// Exercises on the documentation page, rendered by the app's own code.
//
// A classic script rather than a module, because MkDocs emits plain <script>
// tags -- so it captures its own URL while it still can and resolves the
// renderers relative to that, which keeps the page working wherever the site is
// mounted (GitHub Pages puts it under /repetita/).
//
// Nothing here grades anything: there is no server behind this page, and the
// payloads are examples. Answering does nothing, on purpose.

(() => {
  const here = document.currentScript?.src;
  if (!here) return;

  const load = (path) => import(new URL(`./repetita/${path}`, here).href);

  const hydrate = async () => {
    const blocks = document.querySelectorAll(".demo[data-card]:not([data-done])");
    if (!blocks.length) return;

    const modes = Object.fromEntries(
      await Promise.all(
        ["typein", "wordbank", "choice", "flashcard"].map(async (name) => [
          name,
          await load(`modes/${name}.js`),
        ]),
      ),
    );

    for (const block of blocks) {
      let card;
      try {
        card = JSON.parse(block.dataset.card);
      } catch {
        continue;
      }
      const mode = modes[card.form] || modes.typein;
      block.replaceChildren(mode.render(card, () => {}));
      block.append(
        Object.assign(document.createElement("p"), {
          className: "demo-note",
          textContent: "An example. Nothing here is graded.",
        }),
      );
      block.dataset.done = "1";
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hydrate);
  } else {
    hydrate();
  }
  // Material swaps pages without reloading when instant navigation is on.
  document.addEventListener("DOMContentSwitch", hydrate);
})();
