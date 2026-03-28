"""Business logic for Alpaca watchlist CRUD and quote fetching.

All Alpaca calls go through the foundation ``AlpacaClient`` — never
the SDK directly.  Quote data is fetched via ``yfinance`` and cached
in-memory with a configurable TTL to respect rate limits.
"""

from __future__ import annotations

import time
from typing import Any

import yfinance as yf

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_models import WatchlistInfo

# ── Quote cache (module-level, not session state, so tests can reset) ────

_quote_cache: dict[str, Any] = {}
# Structure: { "AAPL": {"data": {...}, "ts": <timestamp>}, ... }


# ── CRUD operations ──────────────────────────────────────────────────────


def get_watchlists(client: AlpacaClient) -> list[WatchlistInfo]:
    """Return all watchlists for the connected account."""
    return client.get_watchlists()


def create_watchlist(
    client: AlpacaClient,
    name: str,
    symbols: list[str] | None = None,
) -> WatchlistInfo:
    """Create a new watchlist, rejecting empty or duplicate names."""
    if not name or not name.strip():
        raise ValueError("Watchlist name must not be empty.")

    existing = client.get_watchlists()
    for wl in existing:
        if wl.name == name:
            raise ValueError(f"A watchlist named '{name}' already exists.")

    return client.create_watchlist(name, symbols or [])


def add_symbol(client: AlpacaClient, watchlist_id: str, symbol: str) -> WatchlistInfo:
    """Add a symbol (uppercased) to a watchlist."""
    if not symbol or not symbol.strip():
        raise ValueError("symbol must not be empty.")
    return client.add_to_watchlist(watchlist_id, symbol.strip().upper())


def remove_symbol(client: AlpacaClient, watchlist_id: str, symbol: str) -> WatchlistInfo:
    """Remove a symbol (uppercased) from a watchlist."""
    return client.remove_from_watchlist(watchlist_id, symbol.strip().upper())


def delete_watchlist(client: AlpacaClient, watchlist_id: str) -> None:
    """Delete a watchlist by ID."""
    client.delete_watchlist(watchlist_id)


def rename_watchlist(client: AlpacaClient, watchlist_id: str, new_name: str) -> WatchlistInfo:
    """Rename a watchlist, rejecting empty or duplicate names."""
    if not new_name or not new_name.strip():
        raise ValueError("Watchlist name must not be empty.")

    existing = client.get_watchlists()
    for wl in existing:
        if wl.name == new_name and wl.watchlist_id != watchlist_id:
            raise ValueError(f"A watchlist named '{new_name}' already exists.")

    return client.update_watchlist(watchlist_id, new_name)  # type: ignore[attr-defined, no-any-return]


# ── Quote fetching with TTL cache ────────────────────────────────────────


def _fetch_quotes_raw(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Fetch live quotes from yfinance.  Internal — call ``fetch_quotes`` instead."""
    result: dict[str, dict[str, float]] = {}
    if not symbols:
        return result

    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                ticker = tickers.tickers.get(sym)
                if ticker is None:
                    continue
                info = ticker.fast_info
                price = float(getattr(info, "last_price", 0) or 0)
                prev_close = float(getattr(info, "previous_close", 0) or 0)
                change = price - prev_close if prev_close else 0
                change_pct = (change / prev_close * 100) if prev_close else 0
                volume = int(getattr(info, "last_volume", 0) or 0)
                result[sym] = {
                    "price": price,
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                    "volume": volume,
                }
            except Exception:
                continue
    except Exception:
        pass

    return result


def fetch_quotes(
    symbols: list[str],
    ttl_seconds: int = 30,
) -> dict[str, dict[str, float]]:
    """Return cached quotes, refreshing from yfinance when stale.

    Parameters
    ----------
    symbols:
        Ticker symbols to fetch quotes for.
    ttl_seconds:
        Cache time-to-live in seconds.  Set to 0 to always re-fetch.
    """
    if not symbols:
        return {}

    now = time.time()

    # Check if ALL requested symbols are fresh in cache
    all_cached = True
    for sym in symbols:
        entry = _quote_cache.get(sym)
        if entry is None or (now - entry["ts"]) > ttl_seconds:
            all_cached = False
            break

    if all_cached:
        return {sym: _quote_cache[sym]["data"] for sym in symbols if sym in _quote_cache}

    # Fetch fresh data
    raw = _fetch_quotes_raw(symbols)

    # Update cache
    now = time.time()
    for sym, data in raw.items():
        _quote_cache[sym] = {"data": data, "ts": now}

    return raw
