"""
Opaque card handles.

Card ids are authored from the material, so a quarter of a real corpus carries
its answer in its id. Filtering fields cannot help: an id is not a field, and it
has to travel to the client so an answer can be posted back for it.
"""

from repetita import store
from repetita.web.handles import Handles


def test_a_handle_is_not_the_card_id():
    h = Handles(["obrigado#produce"])
    assert h.handle("obrigado#produce") != "obrigado#produce"


def test_a_handle_contains_nothing_of_the_card_id():
    # The whole point: no substring of the material survives into the token.
    h = Handles(["licao-2026-09-06-vocab.alguem-bateu#fill"])
    token = h.handle("licao-2026-09-06-vocab.alguem-bateu#fill")
    for fragment in ("alguem", "bateu", "vocab", "licao", "fill"):
        assert fragment not in token.lower()


def test_it_round_trips():
    ids = ["a#x", "b#y", "c#z"]
    h = Handles(ids)
    for card_id in ids:
        assert h.card(h.handle(card_id)) == card_id


def test_handles_are_unique_per_card():
    ids = [f"n{i}#fill" for i in range(500)]
    h = Handles(ids)
    assert len({h.handle(i) for i in ids}) == len(ids)


def test_the_same_card_keeps_one_handle_within_a_run():
    # Otherwise the session and the answer would disagree about what was asked.
    h = Handles(["a#x"])
    assert h.handle("a#x") == h.handle("a#x")


def test_without_a_database_each_map_is_its_own():
    # No connection means nothing to read from or write to, so two maps are
    # independent. This is the shape the unit tests use; the served app always
    # passes a connection.
    assert Handles(["a#x"]).handle("a#x") != Handles(["a#x"]).handle("a#x")


def test_a_handle_survives_a_restart(tmp_path):
    """
    The property the offline queue depends on.

    An answer given offline is posted when the connection returns. If the server
    restarted in between and handles were per-run, that token would resolve to
    nothing and a real answer would be lost. Nothing in the guarantee needed
    regeneration: a token is random and says nothing about the material whether
    it lives for an hour or a year.
    """
    con = store.connect(tmp_path / "t.db")
    first = Handles(["a#x", "b#y"], con=con)
    token = first.handle("a#x")
    con.close()

    con = store.connect(tmp_path / "t.db")
    second = Handles(["a#x", "b#y"], con=con)
    assert second.card(token) == "a#x"
    assert second.handle("a#x") == token
    con.close()


def test_a_card_added_later_gets_a_persisted_handle(tmp_path):
    con = store.connect(tmp_path / "t.db")
    token = Handles([], con=con).handle("late#fill")
    con.close()

    con = store.connect(tmp_path / "t.db")
    assert Handles([], con=con).card(token) == "late#fill"
    con.close()


def test_persistence_does_not_leak_the_card_id(tmp_path):
    # The whole point survives the change: what is stored is a random token, and
    # what the client sees is still nothing about the material.
    con = store.connect(tmp_path / "t.db")
    token = Handles(["licao-2026-09-06-vocab.alguem-bateu#fill"], con=con).handle(
        "licao-2026-09-06-vocab.alguem-bateu#fill"
    )
    con.close()
    for fragment in ("alguem", "bateu", "vocab", "licao", "fill"):
        assert fragment not in token.lower()


def test_an_unknown_handle_resolves_to_nothing():
    h = Handles(["a#x"])
    assert h.card("not-a-real-handle") is None
    assert h.card("") is None


def test_a_card_added_after_startup_still_gets_a_handle():
    h = Handles([])
    token = h.handle("late#fill")
    assert h.card(token) == "late#fill"
