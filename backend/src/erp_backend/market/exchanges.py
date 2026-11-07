"""Exchanges a listing can be manually registered on (D56).

A small, versioned, code-level registry (the Blueprint's ``ref.exchanges`` table is not built
yet). Each entry names the ISO 10383 operating-segment MIC, the display name stored in
``market.securities.exchange_name`` (matching what the SEC path stores for US venues), the
venue's country, and the currencies listings on it trade in. A currency outside that list
is rejected: registration never assumes or converts a currency, and an unusual one must be
added here deliberately, with a reviewed source.

Extending the registry is a code change with a test, not a runtime action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    mic: str
    name: str
    country: str  # ISO 3166-1 alpha-2
    currencies: tuple[str, ...]


EXCHANGES: dict[str, Exchange] = {
    e.mic: e
    for e in (
        Exchange("XNYS", "NYSE", "US", ("USD",)),
        Exchange("XNAS", "Nasdaq", "US", ("USD",)),
        Exchange("XASE", "NYSE American", "US", ("USD",)),
        Exchange("ARCX", "NYSE Arca", "US", ("USD",)),
        # TSX also lists some USD-denominated securities (typically ".U" tickers).
        Exchange("XTSE", "TSX", "CA", ("CAD", "USD")),
        Exchange("XTSX", "TSX Venture", "CA", ("CAD",)),
    )
}
