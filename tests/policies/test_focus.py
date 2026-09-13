"""
Skupienie: a style may narrow the debt (ADR-0018).

This is the one place in the engine where a preference removes cards rather than
reordering them, and ADR-0007 rejected it in a plan for a reason worth quoting:
"a learner who could hide 200 owed cards behind a priority list would, once, and
then find them again a month later at four times the size."

So the tests that matter here are not "does it filter". They are: can it hide
something quietly, and can it hide it forever. The answers have to be no.
"""

from __future__ import annotations

import datetime as dt
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.policies import context, daily
from repetita.store import styles as S

DAY = dt.date(2026, 9, 6)
AT = dt.datetime(2026, 9, 6, 20, 0, tzinfo=dt.UTC)

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
    (root / "course.yaml").write_text(HEAD + "path:\n  - unit: alfa\n  - unit: beta\n")
    for unit in ("alfa", "beta"):
        d = root / "units" / unit / "notes"
        d.mkdir(parents=True)
        (d / "a.yaml").write_text(textwrap.dedent(_notes(unit)))
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    # Answer everything twice so it is all owed rather than all new: a focus is
    # about the debt, and a course nobody has touched has none.
    for card in daily.scheduled_cards(c, "t", user_id=1) * 2:
        store.record_answer(
            c, card.card_id, Rating.GOOD, backend=srs.get("sm2"), at=AT, local_day=DAY
        )
    return c


LATER = DAY + dt.timedelta(days=30)


def _recipe(con, style):
    return context.recipe_for(con, style, LATER, course="t", user_id=1)


def _session(con, style):
    return daily.build_session(con, LATER, course="t", user_id=1, recipe=_recipe(con, style))


ONLY_ALFA = S.Style(focus="unit=alfa")


class TestItNarrows:
    def test_a_focus_narrows_the_queue(self, con):
        assert _session(con, S.DEFAULT).cards
        served = _session(con, ONLY_ALFA).cards
        assert served
        assert all(c.startswith("alfa") for c in served)

    def test_the_session_reports_what_it_hid(self, con):
        session = _session(con, ONLY_ALFA)
        assert session.hidden > 0
        assert session.focus == "unit=alfa"

    def test_a_focus_that_hides_nothing_still_says_it_is_on(self, con):
        # A guardrail that goes quiet while it is not biting is one you forget
        # you turned on. `focus` is reported whatever `hidden` happens to be.
        both = S.Style(focus="unit=alfa,unit=beta")
        session = _session(con, both)
        assert session.hidden == 0
        assert session.focus


class TestItCannotHideQuietly:
    def test_a_focus_never_narrows_owed_count(self, con):
        # The guardrail, and it is enforced by a signature rather than by care:
        # `owed_count` has no parameter a focus could reach it through.
        before = daily.owed_count(con, LATER, course="t", user_id=1)
        S.save(con, "t", ONLY_ALFA, user_id=1)
        assert daily.owed_count(con, LATER, course="t", user_id=1) == before

    def test_owed_hidden_counts_the_gap_by_name(self, con):
        recipe = _recipe(con, ONLY_ALFA)
        assert recipe.focus_ids is not None
        hidden = daily.owed_hidden(con, LATER, recipe.focus_ids, course="t", user_id=1)
        assert hidden == _session(con, ONLY_ALFA).hidden
        assert hidden > 0

    def test_the_forecast_is_the_true_debt_unless_asked_otherwise(self, con):
        recipe = _recipe(con, ONLY_ALFA)
        true = daily.forecast(con, LATER, course="t", user_id=1)
        focused = daily.forecast(con, LATER, course="t", user_id=1, focus_ids=recipe.focus_ids)
        assert true[0] > focused[0], "the gap is what the focus costs"


class TestItCannotHideForever:
    def test_a_focus_lapses_on_its_own_date(self, con):
        style = S.Style(focus="unit=alfa", focus_until=LATER.isoformat())
        assert style.active_focus(LATER) == "unit=alfa"
        assert style.active_focus(LATER + dt.timedelta(days=1)) == ""

    def test_a_lapsed_focus_serves_the_whole_debt_again(self, con):
        style = S.Style(focus="unit=alfa", focus_until=(LATER - dt.timedelta(days=1)).isoformat())
        assert _recipe(con, style).focus_ids is None
        assert _session(con, style).hidden == 0

    def test_a_lapsed_focus_writes_nothing(self, con):
        # Lapsing is a read. A read that wrote would file the lapse as an edit at
        # whatever moment the page happened to load, and put a revision in the
        # log that the learner did not make.
        style = S.Style(focus="unit=alfa", focus_until="2026-01-01")
        S.save(con, "t", style, user_id=1)
        before = len(S.history(con, "t", user_id=1))
        stamp = con.execute("SELECT updated_at FROM study_styles").fetchone()["updated_at"]

        _session(con, S.get(con, "t", user_id=1))

        assert len(S.history(con, "t", user_id=1)) == before
        assert con.execute("SELECT updated_at FROM study_styles").fetchone()["updated_at"] == stamp
        # And the row still says what the learner wrote, so it can be renewed.
        assert S.get(con, "t", user_id=1).focus == "unit=alfa"

    def test_an_unparseable_date_does_not_mean_forever(self, con):
        assert S.Style(focus="unit=alfa", focus_until="nonsense").active_focus(LATER) == ""

    def test_a_bad_date_is_refused_on_the_way_in(self, con):
        with pytest.raises(S.BadStyle, match="not a date"):
            S.save(con, "t", S.Style(focus="unit=alfa", focus_until="nonsense"), user_id=1)

    def test_a_malformed_selector_is_refused(self, con):
        with pytest.raises(S.BadStyle):
            S.save(con, "t", S.Style(focus="this is not a selector"), user_id=1)
