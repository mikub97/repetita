"""
The inbox: material captured before it is shaped.

This module deliberately does the opposite of everything else in `store`. It
accepts text nobody has validated, in no particular shape, and stores it
unchanged. ADR-0009 says why; these tests pin the two properties that make it
worth having -- nothing is altered on the way in, and nothing is lost on the way
out.
"""

from __future__ import annotations

import pytest

from repetita import store
from repetita.store import drafts

RAW = "lekcja 11.09 — futuro simples\n  vou + infinitivo\n  ex: vou estudar amanhã\n"


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


class TestCapturing:
    def test_it_keeps_what_was_typed_byte_for_byte(self, con):
        drafts.capture(con, RAW)
        assert [d.body for d in drafts.queued(con)] == [RAW]

    def test_the_summary_is_the_first_line(self, con):
        # What the list and the button badge show. The first line of a lesson
        # note is nearly always its date and subject, which is exactly what you
        # need to recognise one in a list of five.
        assert drafts.capture(con, RAW).summary == "lekcja 11.09 — futuro simples"

    def test_several_notes_queue_in_the_order_they_arrived(self, con):
        drafts.capture(con, "first")
        drafts.capture(con, "second")
        assert [d.body for d in drafts.queued(con)] == ["first", "second"]


class TestTheLoop:
    def test_closing_one_records_what_came_of_it(self, con):
        # The point of the outcome: a draft is provenance. Six weeks later the
        # question is "where did this exercise come from", and a closed draft
        # with no note answers it no better than a deleted one.
        d = drafts.capture(con, RAW)
        drafts.done(con, d.id, outcome="made 6 gap exercises in licao-2026-09-11")

        closed = drafts.get(con, d.id)
        assert closed is not None
        assert not closed.queued
        assert closed.outcome == "made 6 gap exercises in licao-2026-09-11"

    def test_a_closed_draft_leaves_the_queue_but_not_the_database(self, con):
        d = drafts.capture(con, RAW)
        drafts.done(con, d.id)
        assert drafts.queued(con) == []
        assert [x.id for x in drafts.all_drafts(con)] == [d.id]
        assert drafts.get(con, d.id).body == RAW

    def test_discarding_is_the_one_thing_that_removes_it(self, con):
        # An explicit "this was a mistake", distinct from "this was processed".
        d = drafts.capture(con, "typed into the wrong window")
        assert drafts.discard(con, d.id)
        assert drafts.all_drafts(con) == []

    def test_closing_something_that_is_not_there_says_so(self, con):
        assert drafts.done(con, 999) is None
        assert not drafts.discard(con, 999)
