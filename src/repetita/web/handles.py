"""
Opaque handles for cards.

Card ids are scheduling keys and are authored from the material: a note teaching
`obrigado` is called `obrigado`, and its production card is `obrigado#produce`.
The id therefore *is* the answer for a quarter of a real corpus -- measured at
167 of 676 cards on the first course imported into this engine.

Filtering fields does not help, because the id is not a field. It has to reach
the client so an answer can be posted back for it. So the client never sees it:
it gets a random handle, and the server resolves the handle when the answer comes
in. The id stays a scheduling key, on the server, where it belongs.

**Handles are persisted, not minted per run.** They were per-run originally, on
the argument that a question open across a restart should make the client refetch
anyway. Adding an offline answer queue made that costly: an answer given while
offline is posted when the connection returns, and if the server restarted in
between, a per-run handle resolves to nothing and a real answer is lost. Nothing
in the guarantee depended on regeneration -- a token is random and says nothing
about the material whether it lives for an hour or a year. See ADR-0005.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterable

#: 12 characters of url-safe base64. Long enough that guessing one is pointless,
#: short enough to read in a network tab while debugging.
HANDLE_BYTES = 9


class Handles:
    """A bidirectional map between card ids and the tokens the client sees."""

    def __init__(self, card_ids: Iterable[str], con: sqlite3.Connection | None = None) -> None:
        self._card_of: dict[str, str] = {}
        self._handle_of: dict[str, str] = {}
        self._con = con

        if con is not None:
            for row in con.execute("SELECT card_id, handle FROM card_handles"):
                self._card_of[row["handle"]] = row["card_id"]
                self._handle_of[row["card_id"]] = row["handle"]

        minted = [
            (card_id, self._mint(card_id)) for card_id in card_ids if card_id not in self._handle_of
        ]
        if con is not None and minted:
            with con:
                con.executemany(
                    "INSERT OR IGNORE INTO card_handles(card_id, handle) VALUES(?, ?)", minted
                )

    def _mint(self, card_id: str) -> str:
        token = secrets.token_urlsafe(HANDLE_BYTES)
        while token in self._card_of:  # pragma: no cover -- 72 bits
            token = secrets.token_urlsafe(HANDLE_BYTES)
        self._card_of[token] = card_id
        self._handle_of[card_id] = token
        return token

    def handle(self, card_id: str) -> str:
        """The token for a card, minting one for a card added since startup."""
        token = self._handle_of.get(card_id)
        if token is None:
            token = self._mint(card_id)
            if self._con is not None:
                with self._con:
                    self._con.execute(
                        "INSERT OR IGNORE INTO card_handles(card_id, handle) VALUES(?, ?)",
                        (card_id, token),
                    )
        return token

    def card(self, handle: str) -> str | None:
        """The card a token stands for, or None if it is unknown."""
        return self._card_of.get(handle)

    def __len__(self) -> int:
        return len(self._card_of)
