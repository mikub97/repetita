// How each kind of exercise is drawn.
//
// A *form* (`modes/`) is how you answer: typing, a word bank, a choice, a
// self-rating. A *type* is what the exercise is: a gap in a sentence, a phrase
// to say, a word in two directions. Those are different questions, and until now
// only the first had files -- every type's question went through one generic
// renderer that printed the asked fields largest-first and hoped.
//
// One file per type, registered here. A type with no file falls back to the
// generic renderer, which is the right default: most exercises are a question
// and some context, and a view is only worth writing when a type has a shape the
// default cannot show.
//
// The interface is one function:
//
//     export const notetype = "gap";
//     export function question(card) { ... returns a Node }
//
// `card` is the payload `serialize.public_card` builds -- `ask`, `fields`,
// `notetype`, `template`, `form`. It never contains the answer while the
// question is open, and nothing here may reach for one.

import { question as generic } from "../dom.js";

import * as gap from "./gap.js";
import * as phrase from "./phrase.js";
import * as picture from "./picture.js";
import * as sentence from "./sentence.js";
import * as transform from "./transform.js";
import * as vocab from "./vocab.js";

const VIEWS = Object.fromEntries(
  [gap, phrase, picture, sentence, transform, vocab].map((view) => [view.notetype, view]),
);

export function viewFor(notetype) {
  return VIEWS[notetype] || null;
}

export function names() {
  return Object.keys(VIEWS).sort();
}

// What every form module calls. Same name and same shape as the generic one it
// replaces, so the four `modes/` files each changed by one import line.
export function question(card) {
  const view = VIEWS[card?.notetype];
  return view ? view.question(card) : generic(card);
}
