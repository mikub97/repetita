"""
A saved style, and the one invariant the whole change rests on.

`test_no_style_is_exactly_the_default_session` is why this file exists. Every
other test here is about a setting doing what it says; that one is about the
learner who has never opened the screen getting the session they had before the
screen existed -- asserted by building it both ways and comparing, rather than
argued in a docstring.
"""

from __future__ import annotations

import datetime as dt
import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.policies import context, daily
from repetita.store import styles as S

DAY = dt.date(2026, 9, 6)

HEAD = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""


def _notes(unit, n=3):
    return "notetype: gap\nnotes:\n" + "".join(
        f"  - id: {unit}-{i}\n    prompt: Eu ___ {unit}{i}.\n    answers: [saio]\n    cue: w {i}\n"
        for i in range(n)
    )


@pytest.fixture
def con(tmp_path):
    root = tmp_path / "course"
    root.mkdir(parents=True)
    (root / "course.yaml").write_text(HEAD + "path:\n  - unit: beta\n  - unit: alfa\n")
    for unit in ("alfa", "beta"):
        d = root / "units" / unit / "notes"
        d.mkdir(parents=True)
        (d / "a.yaml").write_text(textwrap.dedent(_notes(unit)))
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    return c


def _session(con, style=None, **kw):
    recipe = None if style is None else context.recipe_for(con, style, DAY, course="t", user_id=1)
    return daily.build_session(con, DAY, course="t", user_id=1, recipe=recipe, **kw)


class TestTheInvariant:
    def test_no_style_is_exactly_the_default_session(self, con):
        # Not "approximately what it was". The same list.
        assert _session(con).cards == _session(con, S.DEFAULT).cards

    def test_the_default_recipe_is_empty(self, con):
        # `recipe_for` short-circuits on the default rather than walking a recipe
        # that says "do what you already do", so this is also the fast path.
        assert context.recipe_for(con, S.DEFAULT, DAY, course="t", user_id=1) == context.Recipe()

    def test_a_database_with_no_row_reads_as_the_default(self, con):
        assert S.get(con, "t") == S.DEFAULT


class TestModes:
    @pytest.mark.parametrize("name", sorted(S.MODES))
    def test_every_mode_builds_a_session(self, con, name):
        style = S.MODES[name]
        if style.introductions == "axis":
            pytest.skip("needs a course that declares an ordered axis")
        assert _session(con, style).cards

    @pytest.mark.parametrize("name", sorted(S.MODES))
    def test_every_mode_is_valid_on_its_own_terms(self, con, name):
        # A preset that its own validator refuses would be a dial that cannot be
        # turned, shipped as a button.
        S.save(con, "t", S.MODES[name], user_id=1)

    def test_sciezka_follows_the_path_and_kurs_does_not_have_to(self, con):
        by_path = _session(con, S.MODES["sciezka"]).cards
        assert by_path
        first = daily.scheduled_cards(con, "t", user_id=1)[0]
        assert first.unit == "beta", "the declared path puts beta first"

    def test_a_batch_knob_caps_the_session(self, con):
        small = S.Style(knobs={"batch": 2})
        assert len(_session(con, small).cards) == 2

    def test_an_explicit_limit_beats_the_knob(self, con):
        # A request asking for a size is a fact about the request; a knob is a
        # preference about a session. The request wins.
        big = S.Style(knobs={"batch": 2})
        assert len(_session(con, big, limit=5).cards) > 2


class TestNaming:
    def test_a_recipe_that_matches_a_preset_takes_its_name(self):
        assert S.named(S.Style(introductions="course")).mode == "sciezka"

    def test_anything_else_is_custom(self):
        assert S.named(S.Style(knobs={"batch": 77})).mode == S.CUSTOM

    def test_the_name_is_compared_by_recipe_not_trusted(self):
        # A style claiming to be a preset while running something else would
        # leave somebody labelled with settings they are not using.
        lying = S.Style(mode="spokojny", knobs={"batch": 999})
        assert S.named(lying).mode == S.CUSTOM


class TestStore:
    def test_a_style_is_per_person(self, con):
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        assert S.get(con, "t", user_id=2) == S.DEFAULT

    def test_a_style_is_per_course(self, con):
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        assert S.get(con, "other", user_id=1) == S.DEFAULT

    def test_revisions_survive_a_reset_to_the_default(self, con):
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        S.save(con, "t", S.DEFAULT, user_id=1)
        assert len(S.history(con, "t", user_id=1)) == 2
        assert S.get(con, "t", user_id=1) == S.DEFAULT

    def test_the_latest_revision_is_the_one_an_answer_files_under(self, con):
        assert S.latest_revision(con, "t", user_id=1) is None
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        first = S.latest_revision(con, "t", user_id=1)
        S.save(con, "t", S.DEFAULT, user_id=1)
        assert S.latest_revision(con, "t", user_id=1) > first

    def test_an_ordering_by_plan_with_no_plan_is_refused(self, con):
        with pytest.raises(S.BadStyle, match="needs a plan"):
            S.save(con, "t", S.Style(introductions="plan"), user_id=1)

    def test_an_ordering_by_axis_with_no_axis_is_refused(self, con):
        with pytest.raises(S.BadStyle, match="needs an axis"):
            S.save(con, "t", S.Style(introductions="axis"), user_id=1)

    def test_a_deleted_plan_falls_back_and_says_so(self, con):
        recipe = context.recipe_for(con, S.Style(plan_id=9999), DAY, course="t", user_id=1)
        assert recipe.plan_missing
        assert not recipe.weight


class TestWhatAStyleProduced:
    """
    The payoff for writing `style_revision_id` before anything read it.

    A column added in six months leaves every answer before it unattributable,
    which is why the write side shipped first. This is the read side.
    """

    def _answer(self, con, n, revision):
        from repetita import srs
        from repetita.core.types import Rating

        at = dt.datetime(2026, 9, 6, 20, 0, tzinfo=dt.UTC)
        cards = daily.scheduled_cards(con, "t", user_id=1)
        for i in range(n):
            store.record_answer(
                con,
                cards[i % len(cards)].card_id,
                Rating.GOOD if i % 2 else Rating.AGAIN,
                backend=srs.get("sm2"),
                at=at,
                local_day=DAY,
                style_revision_id=revision,
                user_id=1,
            )

    def test_it_reports_what_was_answered_under_each_revision(self, con):
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        first = S.latest_revision(con, "t", user_id=1)
        self._answer(con, 4, first)
        S.save(con, "t", S.DEFAULT, user_id=1)

        rows = S.spells(con, "t", user_id=1)
        assert [r.revision for r in rows] == sorted([r.revision for r in rows], reverse=True)
        under = {r.revision: r for r in rows}
        assert under[first].answers == 4
        assert under[first].accuracy == 0.5

    def test_a_revision_nobody_studied_under_is_still_listed(self, con):
        # A setting tried and abandoned within the hour is a real thing to show.
        # Dropping it would make the list a history of settings that worked.
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        rows = S.spells(con, "t", user_id=1)
        assert len(rows) == 1
        assert rows[0].answers == 0
        assert rows[0].accuracy is None

    def test_it_carries_the_settings_that_were_in_force(self, con):
        S.save(con, "t", S.Style(introductions="course", knobs={"batch": 7}), user_id=1)
        spell = S.spells(con, "t", user_id=1)[0]
        assert spell.style.introductions == "course"
        assert spell.style.knobs["batch"] == 7

    def test_an_answer_under_a_plan_is_not_counted_here(self, con):
        # The whole reason for two columns. A plan answer filed under a style
        # would make every comparison on this screen wrong.
        S.save(con, "t", S.Style(debt="weakest"), user_id=1)
        self._answer(con, 3, None)
        assert S.spells(con, "t", user_id=1)[0].answers == 0

    def test_the_mode_tells_the_truth_however_it_was_saved(self, con):
        # Naming used to happen only in the web layer, so a style saved through
        # the store ran one recipe while calling itself another -- and
        # `repetita styles` printed the name, not the recipe.
        S.save(con, "t", S.Style(introductions="course"), user_id=1)
        assert S.get(con, "t", user_id=1).mode == "sciezka"
        assert S.spells(con, "t", user_id=1)[0].style.mode == "sciezka"
