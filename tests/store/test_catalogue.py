"""
Counting material, grouped by anything.

The query this package exists for is "the A2 grammar cards about prepositions
that are still new" -- which could not be expressed at all while tags lived in a
JSON blob and a card's stage was recomputed in Python.
"""

from __future__ import annotations

import datetime as dt
import textwrap

import pytest

from repetita import srs, store
from repetita.content.loader import load_course
from repetita.core.types import Rating
from repetita.store.catalogue import (
    SelectorError,
    card_ids_for,
    catalogue,
    note_ids_for,
    parse_selector,
)

AT = dt.datetime(2026, 9, 6, 21, 30, tzinfo=dt.UTC)

COURSE = """\
format_version: 1
id: t
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

FACETS = """\
    axes:
      level: {values: [A1, A2], ordered: true, max_per_note: 1}
      track: {values: [vocabulario, gramatica]}
      topic: {catch_all: true}
    """

NOTES = """\
    notetype: vocab
    tags: [A2, vocabulario, comida]
    notes:
      - id: casa
        l2: a casa
        l1: dom
      - id: rua
        l2: a rua
        l1: ulica
        tags: [cidade]
    """


@pytest.fixture
def course_root(tmp_path):
    return tmp_path / "course"


@pytest.fixture
def con(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "facets.yaml").write_text(textwrap.dedent(FACETS))
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    yield c
    c.close()


class TestSelector:
    def test_it_parses_name_equals_value(self):
        assert parse_selector("topic=comida,state=new") == {"topic": ["comida"], "state": ["new"]}

    def test_repeating_a_dimension_widens_rather_than_contradicts(self):
        # Two checkboxes mean "either". Contradiction would make an empty result
        # look like an empty course.
        assert parse_selector("state=new,state=young") == {"state": ["new", "young"]}

    def test_nothing_is_an_empty_filter(self):
        assert parse_selector(None) == {} == parse_selector("")

    @pytest.mark.parametrize("bad", ["nonsense", "=value", "topic=", "1bad=x"])
    def test_it_refuses_what_it_cannot_honour(self, bad):
        with pytest.raises(SelectorError):
            parse_selector(bad)


class TestGrouping:
    def test_by_topic(self, con):
        rows = {r.keys["topic"]: r.cards for r in catalogue(con, group_by=["topic"])}
        assert rows == {"comida": 4, "cidade": 2}

    def test_by_two_dimensions_at_once(self, con):
        rows = {
            (r.keys["track"], r.keys["state"]): r.cards
            for r in catalogue(con, group_by=["track", "state"])
        }
        assert rows == {("vocabulario", "new"): 4}

    def test_state_comes_from_the_stored_bucket(self, con):
        # One GOOD answer under SM-2 leaves the interval at 0, so the card is
        # still being learned rather than young. The count follows the stored
        # bucket, which is the point: there is no second definition in SQL.
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        rows = {r.keys["state"]: r.cards for r in catalogue(con, group_by=["state"])}
        assert rows == {"new": 3, "learning": 1}

    def test_a_filter_narrows_the_count(self, con):
        rows = catalogue(con, group_by=["topic"], where=parse_selector("topic=cidade"))
        assert [(r.keys["topic"], r.cards) for r in rows] == [("cidade", 2)]

    def test_archived_material_is_not_counted(self, con):
        # It has left the course. Counting it would make every total disagree
        # with the queue.
        con.execute("UPDATE cards SET archived_at = '2026-09-10' WHERE note_id = 'rua'")
        con.commit()
        rows = {r.keys["topic"]: r.cards for r in catalogue(con, group_by=["topic"])}
        assert rows == {"comida": 2}


class TestSelecting:
    def test_cards_come_back_in_content_order(self, con):
        assert card_ids_for(con, parse_selector("topic=comida")) == [
            "casa#produce",
            "casa#recognize",
            "rua#produce",
            "rua#recognize",
        ]

    def test_notes_are_what_tagging_operates_on(self, con):
        assert note_ids_for(con, parse_selector("topic=cidade")) == ["rua"]


class TestBucketBackfill:
    def test_a_state_with_no_bucket_gets_one_on_the_next_sync(self, con, tmp_path):
        # A column added by a migration starts NULL, and COALESCE would then
        # report every card a learner has ever answered as new -- a catalogue
        # confidently wrong about their own progress.
        store.record_answer(con, "casa#produce", Rating.GOOD, backend=srs.get("sm2"), at=AT)
        con.execute("UPDATE card_state SET bucket = NULL")
        con.commit()

        store.sync(con, load_course(tmp_path / "course"))

        assert (
            con.execute("SELECT COUNT(*) AS n FROM card_state WHERE bucket IS NULL").fetchone()["n"]
            == 0
        )
