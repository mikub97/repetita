"""
The shell's view table, checked from Python because there is no JS test runner.

Two bugs shipped together and neither was visible to `node --check`, to ruff, to
mypy or to any test: a screen whose hash did not match its key, so linking to it
did nothing, and a screen whose only entry point was hidden in the state
everybody starts in -- so it could not be opened until you had already changed
something on it.

Both are the same shape: a table of routes where one row disagrees with the rest.
That is what a test can hold, so it holds it here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "static"
DESIGNER = (STATIC / "designer.js").read_text()
INDEX = (
    Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "templates" / "index.html"
).read_text()

#: `key: { panel: "...", tab: "...", shell: "...", hash: "..." },`
ROW = re.compile(
    r'^\s*(?P<key>\w+):\s*\{\s*panel:\s*(?P<panel>null|"[^"]*")'
    r'.*?hash:\s*"(?P<hash>[^"]*)"',
    re.M,
)


def _views():
    block = DESIGNER[DESIGNER.index("const VIEWS = {") : DESIGNER.index("export function show")]
    found = {m["key"]: {"panel": m["panel"], "hash": m["hash"]} for m in ROW.finditer(block)}
    assert found, "the VIEWS table moved -- this test is reading the wrong block"
    return found


class TestRouting:
    def test_every_hash_matches_its_key(self):
        # `fromHash` looks the view up by the text after `#`, so a row whose hash
        # is not its key is a screen nothing can link to. `#how` on a row keyed
        # `howstudy` is exactly that, and it silently did nothing.
        for key, view in _views().items():
            if not view["hash"]:
                continue
            assert view["hash"] == f"#{key}", (
                f"view {key!r} has hash {view['hash']!r}; `fromHash` resolves "
                f"`{view['hash']}` to the key {view['hash'].lstrip('#')!r}, which is not a view"
            )

    @pytest.mark.parametrize("key", sorted(_views()))
    def test_every_panel_exists_in_the_page(self, key):
        panel = _views()[key]["panel"]
        if panel == "null":
            return
        name = panel.strip('"')
        assert f'id="{name}"' in INDEX, (
            f"view {key!r} shows panel {name!r}, which the template does not contain"
        )


class TestReachability:
    def test_a_screen_with_no_tab_has_a_visible_way_in(self):
        """
        A view with `tab: ""` is not on the tab bar, so something else has to open
        it. That "something else" must not be hidden in the state a new account
        is in, or the screen cannot be reached at all -- which is what happened
        to "Jak się uczę": its chip was hidden whenever the mode was the default,
        and the only way to leave the default was the screen behind the chip.
        """
        howstudy = (STATIC / "howstudy.js").read_text()
        # The chip is shown unconditionally at the top of the refresh, before any
        # decision about what it says.
        assert "link.hidden = false;" in howstudy
        assert "link.hidden = plain" not in howstudy, "the chicken-and-egg bug is back"
        # And it is reachable from the gear, which is where a person looks for a
        # setting even when a chip is right there.
        assert 'show("howstudy")' in (STATIC / "settings.js").read_text()
