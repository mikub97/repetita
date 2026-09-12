"""
How the app looks, and the one rule a skin must obey.

A theme may only redefine tokens `:root` already declares. That is what keeps
three skins from becoming three stylesheets: a theme that invents `--card-glow`
is a theme with a rule nothing else honours, and the next one will need its own.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from repetita.store import containers
from repetita.web.app import create_app

STATIC = Path(__file__).resolve().parents[2] / "src" / "repetita" / "web" / "static"

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: vocab
    notes:
      - id: a
        l2: um
        l1: jeden
    """


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "c"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    app = create_app(root, db_path=tmp_path / "study.db")
    return app.test_client(), app


class TestChoosing:
    def test_the_default_is_the_current_look(self, client):
        c, _ = client
        body = c.get("/api/settings").get_json()

        assert body["theme"] == "spokojny"
        assert body["themes"] == ["spokojny", "duzy", "cieply"]

    def test_a_choice_is_remembered(self, client):
        c, app = client

        assert c.post("/api/settings", json={"theme": "duzy"}).get_json()["theme"] == "duzy"

        assert c.get("/api/settings").get_json()["theme"] == "duzy"
        # And in the table, under the scope that is not about a course.
        from repetita import store

        con = store.connect(app.config["REPETITA_DB"])
        try:
            assert containers.settings(con, containers.UI) == {"theme": "duzy"}
        finally:
            con.close()

    def test_a_theme_nobody_declared_is_refused(self, client):
        c, _ = client

        assert c.post("/api/settings", json={"theme": "neon"}).status_code == 400
        assert c.get("/api/settings").get_json()["theme"] == "spokojny"

    def test_a_stored_theme_that_no_longer_exists_reads_as_the_default(self, client):
        # A theme can be withdrawn. Somebody who had it chosen should get the
        # default rather than a `data-theme` nothing styles.
        c, app = client
        from repetita import store

        con = store.connect(app.config["REPETITA_DB"])
        try:
            containers.remember(con, containers.UI, {"theme": "withdrawn"})
        finally:
            con.close()

        assert c.get("/api/settings").get_json()["theme"] == "spokojny"


class TestASkinOnlyRedefinesTokens:
    """The rule `docs/design-system.md` asks for, checked rather than trusted."""

    def _css(self) -> str:
        return (STATIC / "style.css").read_text(encoding="utf-8")

    def test_every_token_a_theme_sets_exists_on_root(self):
        css = self._css()
        root = set(re.findall(r"^\s*(--[a-z0-9-]+):", css.split("@media")[0], re.M))
        assert root, "no tokens found on :root"

        for theme, block in re.findall(r'body\[data-theme="([a-z]+)"\]\s*\{([^}]*)\}', css):
            for token in re.findall(r"^\s*(--[a-z0-9-]+):", block, re.M):
                assert token in root, f"{theme} declares {token}, which :root does not"

    def test_the_default_declares_nothing_of_its_own(self):
        # It is what `:root` already says. A block restating the base is a block
        # that drifts from it.
        assert 'body[data-theme="spokojny"]' not in self._css()

    def test_the_themes_the_server_names_are_the_ones_with_styles(self):
        from repetita.web.api import THEMES

        css = self._css()
        for theme in THEMES[1:]:
            assert f'body[data-theme="{theme}"]' in css, f"{theme} has no styles"

    def test_motion_is_behind_the_reduced_motion_query(self):
        css = self._css()
        assert "@keyframes repetita-well-done" in css
        reduced = css[css.index("@media (prefers-reduced-motion") :]
        assert "animation: none" in reduced, "the animation is not switched off for anybody"
