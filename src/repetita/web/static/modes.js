// The vocabulary two screens share: what a knob is called, and what a named
// mode stands for.
//
// It lives in its own module because the Design tab and "Jak się uczę" must
// offer the same dials by the same names. Defined twice, they drift -- one grows
// a slider the other has never heard of, and a learner who set something in one
// place goes looking for it in the other.
//
// A knob says a sentence rather than showing a number, which is the same
// argument `settings.js` makes about theme names: "template_bias: 2.5" tells you
// the name of a variable and nothing about what moving it does.

export const KNOBS = [
  {
    key: "new_every",
    min: 1,
    max: 10,
    step: 1,
    fallback: 3,
    says: (v) => `Jedna nowa karta po każdych ${v}, które już zalegają.`,
  },
  {
    key: "batch",
    min: 10,
    max: 100,
    step: 5,
    fallback: 40,
    says: (v) => `Sesje po mniej więcej ${v} kart.`,
  },
  {
    key: "daily_target",
    min: 5,
    max: 100,
    step: 5,
    fallback: 30,
    says: (v) => `Dzień liczy się jako zrobiony po ${v} odpowiedziach.`,
  },
  {
    key: "gate_threshold",
    min: 0.4,
    max: 0.95,
    step: 0.05,
    fallback: 0.75,
    says: (v) =>
      `Gdy trafiam poniżej ${Math.round(v * 100)}%, wstrzymaj nowy materiał — poza świeżą lekcją.`,
  },
  {
    key: "ladder_steps",
    min: 0,
    max: 2,
    step: 1,
    fallback: 1,
    says: (v) =>
      v === 0
        ? "Pytaj od razu tak, jak zadeklarowano — bez łagodnego pierwszego kontaktu."
        : v === 1
          ? "Pierwsze spotkanie uczy, kolejne sprawdzają."
          : "Dwa pierwsze spotkania uczą, dopiero potem produkcja.",
  },
];

//: `consolidation` is a switch rather than a slider, so it is not in `KNOBS`.
export const SWITCHES = [
  {
    key: "consolidation",
    fallback: true,
    says: (on) =>
      on
        ? "Gdy nie ma nic zaległego ani nowego, dobierz najsłabszy materiał."
        : "Gdy nie ma nic zaległego ani nowego, dzień jest skończony.",
  },
];

export function fallbackFor(key) {
  const knob = KNOBS.find((k) => k.key === key);
  if (knob) return knob.fallback;
  const sw = SWITCHES.find((s) => s.key === key);
  return sw ? sw.fallback : undefined;
}
