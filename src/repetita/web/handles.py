"""
Opaque per-run handles for cards.

Card ids are scheduling keys and are authored from the material: a note teaching
`obrigado` is called `obrigado`, and its production card is `obrigado#produce`.
The id therefore *is* the answer for a quarter of a real corpus -- measured at
167 of 676 cards on the first course imported into this engine.

Filtering fields does not help, because the id is not a field. It has to reach
the client so an answer can be posted back for it. So the client never sees it:
it gets a random handle, and the server resolves the handle when the answer comes
in. The id stays a scheduling key, on the server, where it belongs.

Handles are regenerated on every start. An open question at restart therefore
resolves to nothing and the client refetches, which is the correct outcome
anyway -- the content may have changed under it.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

#: 12 characters of url-safe base64. Long enough that guessing one is pointless,
#: short enough to read in a network tab while debugging.
HANDLE_BYTES = 9


class Handles:
    """A bidirectional map between card ids and the tokens the client sees."""

    def __init__(self, card_ids: Iterable[str]) -> None:
        self._card_of: dict[str, str] = {}
        self._handle_of: dict[str, str] = {}
        for card_id in card_ids:
            token = secrets.token_urlsafe(HANDLE_BYTES)
            while token in self._card_of:  # pragma: no cover -- 72 bits
                token = secrets.token_urlsafe(HANDLE_BYTES)
            self._card_of[token] = card_id
            self._handle_of[card_id] = token

    def handle(self, card_id: str) -> str:
        """The token for a card, minting one for a card added since startup."""
        token = self._handle_of.get(card_id)
        if token is None:
            token = secrets.token_urlsafe(HANDLE_BYTES)
            self._card_of[token] = card_id
            self._handle_of[card_id] = token
        return token

    def card(self, handle: str) -> str | None:
        """The card a token stands for, or None if it is unknown or expired."""
        return self._card_of.get(handle)

    def __len__(self) -> int:
        return len(self._card_of)
