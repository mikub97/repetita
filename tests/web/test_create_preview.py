"""
The Create tab's preview is a picture of the exercise, not a place to answer it.

The bug this file exists for: typing in a field moved the caret out of it and
into the preview, and the rest of the word went there.

It is an interaction between two correct pieces of code. `modes/typein.js` ends
with `queueMicrotask(() => input.focus())`, which is right in Study -- the answer
box should take the caret. `create.js` rebuilds the preview on every keystroke,
which is also right, or the screen goes on saying "not an exercise yet" about an
exercise you have just finished. Together they moved the caret on every
character typed.

Asserted over the source because this repository has no JavaScript test runner
and the behaviour is invisible to every Python test in it. That is a weak test
of a real thing, and a weak test here beats the alternative: the whole suite
passing while the Create tab cannot be typed into.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "static"


def test_a_mode_still_takes_the_caret_when_it_renders():
    # Half of the interaction. If this ever stops being true the guard below is
    # unnecessary -- and this test failing is how anybody would find that out,
    # rather than discovering it years later while deleting the guard.
    js = (STATIC / "modes" / "typein.js").read_text(encoding="utf-8")
    assert "focus()" in js, "typein no longer focuses; the preview guard may be obsolete"


def test_the_preview_card_is_inert():
    js = (STATIC / "create.js").read_text(encoding="utf-8")
    block = re.search(r'class:\s*"cpreview-card".*?\}', js, flags=re.S)
    assert block, "cpreview-card is no longer built with an attribute object"
    # `inert:` with the colon, and with comments stripped first. Matching the
    # bare word passed on the comment that explains why the attribute is there,
    # which made this test worthless until removing the attribute failed to
    # break it.
    code = re.sub(r"//[^\n]*", "", block.group(0))
    assert re.search(r"\binert\s*:", code), (
        "the preview renders the real study form, which focuses itself -- "
        "without `inert` every keystroke in a field moves the caret into it"
    )


def test_the_preview_renders_the_real_form_rather_than_a_drawing():
    # Why the guard is needed at all, pinned so that "just draw a picture
    # instead" is a visible change rather than a silent one.
    js = (STATIC / "create.js").read_text(encoding="utf-8")
    assert "MODES[form].render(" in js


def test_the_tabs_above_the_preview_stay_clickable():
    # `inert` is on the card, not the box. Putting it on the box would take the
    # card and form pickers with it, which is a different bug with the same
    # symptom: a control that does nothing.
    js = (STATIC / "create.js").read_text(encoding="utf-8")
    box = re.search(r'class:\s*"cpreview-box"', js)
    assert box, "cpreview-box is gone; check what replaced it"
    head = js[box.start() : box.start() + 200]
    assert "inert" not in head, "inert on the whole box would disable the preview's own tabs"
