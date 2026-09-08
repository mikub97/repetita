"""
The server and the client must agree on which forms exist.

This file exists because they did not, and nothing noticed. `choice` was added to
`SUPPORTED_FORMS`, the serialiser started shipping `options`, and no `choice.js`
was ever written -- so the client hit `MODES[card.form] === undefined` and
**silently skipped the card**. Measured on the real corpus: 546 of 676 cards at
first contact.

`tests/web/test_api.py` asserted that a served form is in `SUPPORTED_FORMS`, and
passed, because the gap was *between* the server's list and the client's. Only a
test that reads both can see it.

Parsing JavaScript with a regex from a Python test is ugly. It is also the only
place these two worlds can be compared without a browser, and the alternative --
finding out in a session, from a status line rather than an error -- is worse.
"""

import re
from pathlib import Path

import pytest

from repetita.web.serialize import SUPPORTED_FORMS

STATIC = Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "static"
MODES_DIR = STATIC / "modes"
APP_JS = STATIC / "app.js"

#: `export const form = "typein";`
_EXPORTED = re.compile(r'export\s+const\s+form\s*=\s*["\']([a-z_]+)["\']')
#: the module names inside `[choice, typein, …].map(`
_REGISTRY = re.compile(r"\[([a-zA-Z0-9_,\s]+)\]\.map\(\(mode\)", re.S)
#: `import * as typein from "./modes/typein.js";`
_IMPORT = re.compile(r'import\s+\*\s+as\s+(\w+)\s+from\s+["\']\./modes/([a-z_]+)\.js["\']')


def _module_forms() -> dict[str, str]:
    """Module file stem -> the form name it declares."""
    out = {}
    for path in sorted(MODES_DIR.glob("*.js")):
        found = _EXPORTED.search(path.read_text(encoding="utf-8"))
        assert found, f"{path.name} declares no `export const form`"
        out[path.stem] = found.group(1)
    return out


def _registered_forms() -> set[str]:
    """The forms actually reachable from `MODES` in app.js."""
    source = APP_JS.read_text(encoding="utf-8")
    imported = {alias: stem for alias, stem in _IMPORT.findall(source)}
    registry = _REGISTRY.search(source)
    assert registry, "could not find the MODES registry in app.js"
    aliases = [name.strip() for name in registry.group(1).split(",") if name.strip()]

    forms = _module_forms()
    out = set()
    for alias in aliases:
        assert alias in imported, f"{alias} is in MODES but never imported"
        out.add(forms[imported[alias]])
    return out


def test_every_supported_form_can_be_drawn():
    """
    The failure this file was written for. A form the server will serve and the
    client cannot draw is invisible: the card is skipped with a status message,
    the API returns valid JSON, and every other test passes.
    """
    missing = set(SUPPORTED_FORMS) - _registered_forms()
    assert not missing, (
        f"the server serves {sorted(missing)} but no client module renders them; "
        f"cards in that form are silently skipped"
    )


def test_the_client_draws_nothing_the_server_will_not_serve():
    """The other direction: dead code, and a form nobody can reach."""
    extra = _registered_forms() - set(SUPPORTED_FORMS)
    assert not extra, f"{sorted(extra)} is registered in the client but never served"


def test_every_module_in_the_directory_is_registered():
    """A file in `modes/` that nothing imports is a form that will never appear."""
    declared = set(_module_forms().values())
    unregistered = declared - _registered_forms()
    assert not unregistered, f"{sorted(unregistered)} exists in modes/ but is not in MODES"


@pytest.mark.parametrize("stem", sorted(p.stem for p in MODES_DIR.glob("*.js")))
def test_a_module_renders_and_submits(stem):
    """
    Shape check, not behaviour: a module has to export `render` and hand its
    answer to the callback rather than posting anything itself. Grading is
    server-side, and a module that talks to the API directly would be able to
    route around that.
    """
    source = (MODES_DIR / f"{stem}.js").read_text(encoding="utf-8")
    assert "export function render(" in source, f"{stem}.js exports no render()"
    assert "submit(" in source, f"{stem}.js never calls submit()"
    assert "fetch(" not in source, f"{stem}.js talks to the network; grading is server-side"
