"""
Opaque card handles.

Card ids are authored from the material, so a quarter of a real corpus carries
its answer in its id. Filtering fields cannot help: an id is not a field, and it
has to travel to the client so an answer can be posted back for it.
"""

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


def test_two_runs_mint_different_handles():
    # Handles are per-run by design; an open question at restart resolves to
    # nothing and the client refetches, which is right anyway because the
    # content may have changed under it.
    assert Handles(["a#x"]).handle("a#x") != Handles(["a#x"]).handle("a#x")


def test_an_unknown_handle_resolves_to_nothing():
    h = Handles(["a#x"])
    assert h.card("not-a-real-handle") is None
    assert h.card("") is None


def test_a_card_added_after_startup_still_gets_a_handle():
    h = Handles([])
    token = h.handle("late#fill")
    assert h.card(token) == "late#fill"
