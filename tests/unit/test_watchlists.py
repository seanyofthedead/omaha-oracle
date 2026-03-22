"""Tests for the Alpaca watchlist manager.

TDD — RED phase: every test is written before the implementation in
``dashboard.watchlist_manager``.  All Alpaca API calls are mocked via
``unittest.mock``; no real credentials or network access required.

Covers:
  - Create watchlist (success + duplicate name)
  - Add symbol to watchlist (success + invalid symbol)
  - Remove symbol from watchlist
  - Delete watchlist
  - Rename watchlist (update or delete+recreate fallback)
  - Empty watchlist display
  - Quote fetch with cache hit / miss
  - API error states (timeout, auth failure, rate limit)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from dashboard.alpaca_models import WatchlistInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_client() -> MagicMock:
    """Return a mock AlpacaClient with sensible defaults."""
    client = MagicMock()
    client.get_watchlists.return_value = []
    return client


def _make_watchlist(
    watchlist_id: str = "wl-1",
    name: str = "Tech",
    symbols: list[str] | None = None,
) -> WatchlistInfo:
    return WatchlistInfo(
        watchlist_id=watchlist_id,
        name=name,
        created_at="2026-03-20T10:00:00",
        updated_at="2026-03-20T10:00:00",
        symbols=symbols or [],
    )


# ===========================================================================
# 1. Create watchlist
# ===========================================================================


class TestCreateWatchlist:
    def test_create_watchlist_success(self):
        """Creating a watchlist delegates to client and returns WatchlistInfo."""
        from dashboard.watchlist_manager import create_watchlist

        client = _make_client()
        expected = _make_watchlist(name="My List")
        client.create_watchlist.return_value = expected

        result = create_watchlist(client, "My List")

        client.create_watchlist.assert_called_once_with("My List", [])
        assert result == expected

    def test_create_watchlist_with_initial_symbols(self):
        """Symbols passed at creation are forwarded to the client."""
        from dashboard.watchlist_manager import create_watchlist

        client = _make_client()
        expected = _make_watchlist(name="Faves", symbols=["AAPL", "MSFT"])
        client.create_watchlist.return_value = expected

        result = create_watchlist(client, "Faves", symbols=["AAPL", "MSFT"])

        client.create_watchlist.assert_called_once_with("Faves", ["AAPL", "MSFT"])
        assert result.symbols == ["AAPL", "MSFT"]

    def test_create_watchlist_duplicate_name_raises(self):
        """If a watchlist with the same name exists, raise ValueError."""
        from dashboard.watchlist_manager import create_watchlist

        client = _make_client()
        client.get_watchlists.return_value = [_make_watchlist(name="Dupes")]

        with pytest.raises(ValueError, match="already exists"):
            create_watchlist(client, "Dupes")

        client.create_watchlist.assert_not_called()

    def test_create_watchlist_empty_name_raises(self):
        """Empty or whitespace-only names are rejected."""
        from dashboard.watchlist_manager import create_watchlist

        client = _make_client()

        with pytest.raises(ValueError, match="name"):
            create_watchlist(client, "  ")


# ===========================================================================
# 2. Add symbol to watchlist
# ===========================================================================


class TestAddSymbol:
    def test_add_symbol_success(self):
        from dashboard.watchlist_manager import add_symbol

        client = _make_client()
        updated = _make_watchlist(symbols=["AAPL", "GOOG"])
        client.add_to_watchlist.return_value = updated

        result = add_symbol(client, "wl-1", "GOOG")

        client.add_to_watchlist.assert_called_once_with("wl-1", "GOOG")
        assert "GOOG" in result.symbols

    def test_add_symbol_uppercases_input(self):
        """Symbols are normalised to uppercase before submission."""
        from dashboard.watchlist_manager import add_symbol

        client = _make_client()
        client.add_to_watchlist.return_value = _make_watchlist(symbols=["AAPL"])

        add_symbol(client, "wl-1", "aapl")

        client.add_to_watchlist.assert_called_once_with("wl-1", "AAPL")

    def test_add_symbol_empty_raises(self):
        from dashboard.watchlist_manager import add_symbol

        client = _make_client()

        with pytest.raises(ValueError, match="symbol"):
            add_symbol(client, "wl-1", "")

    def test_add_symbol_api_invalid_symbol(self):
        """API raises for an unknown ticker — surfaces as AlpacaInvalidSymbolError."""
        from alpaca.common.exceptions import APIError

        from dashboard.watchlist_manager import add_symbol

        client = _make_client()
        client.add_to_watchlist.side_effect = APIError("asset not found", 404)

        with pytest.raises(APIError):
            add_symbol(client, "wl-1", "ZZZZZZ")


# ===========================================================================
# 3. Remove symbol from watchlist
# ===========================================================================


class TestRemoveSymbol:
    def test_remove_symbol_success(self):
        from dashboard.watchlist_manager import remove_symbol

        client = _make_client()
        updated = _make_watchlist(symbols=["AAPL"])
        client.remove_from_watchlist.return_value = updated

        result = remove_symbol(client, "wl-1", "GOOG")

        client.remove_from_watchlist.assert_called_once_with("wl-1", "GOOG")
        assert "GOOG" not in result.symbols

    def test_remove_symbol_uppercases(self):
        from dashboard.watchlist_manager import remove_symbol

        client = _make_client()
        client.remove_from_watchlist.return_value = _make_watchlist()

        remove_symbol(client, "wl-1", "goog")

        client.remove_from_watchlist.assert_called_once_with("wl-1", "GOOG")


# ===========================================================================
# 4. Delete watchlist
# ===========================================================================


class TestDeleteWatchlist:
    def test_delete_watchlist_success(self):
        from dashboard.watchlist_manager import delete_watchlist

        client = _make_client()

        delete_watchlist(client, "wl-1")

        client.delete_watchlist.assert_called_once_with("wl-1")

    def test_delete_watchlist_api_error_propagates(self):
        from alpaca.common.exceptions import APIError

        from dashboard.watchlist_manager import delete_watchlist

        client = _make_client()
        client.delete_watchlist.side_effect = APIError("not found", 404)

        with pytest.raises(APIError):
            delete_watchlist(client, "wl-nonexistent")


# ===========================================================================
# 5. Rename watchlist
# ===========================================================================


class TestRenameWatchlist:
    def test_rename_watchlist_success(self):
        from dashboard.watchlist_manager import rename_watchlist

        client = _make_client()
        client.get_watchlists.return_value = [
            _make_watchlist(watchlist_id="wl-1", name="Old Name"),
        ]
        renamed = _make_watchlist(watchlist_id="wl-1", name="New Name")
        client.update_watchlist.return_value = renamed

        result = rename_watchlist(client, "wl-1", "New Name")

        client.update_watchlist.assert_called_once_with("wl-1", "New Name")
        assert result.name == "New Name"

    def test_rename_watchlist_duplicate_name_raises(self):
        from dashboard.watchlist_manager import rename_watchlist

        client = _make_client()
        client.get_watchlists.return_value = [
            _make_watchlist(watchlist_id="wl-1", name="Alpha"),
            _make_watchlist(watchlist_id="wl-2", name="Beta"),
        ]

        with pytest.raises(ValueError, match="already exists"):
            rename_watchlist(client, "wl-1", "Beta")

    def test_rename_watchlist_empty_name_raises(self):
        from dashboard.watchlist_manager import rename_watchlist

        client = _make_client()

        with pytest.raises(ValueError, match="name"):
            rename_watchlist(client, "wl-1", "")


# ===========================================================================
# 6. List watchlists / empty display
# ===========================================================================


class TestListWatchlists:
    def test_get_watchlists_returns_list(self):
        from dashboard.watchlist_manager import get_watchlists

        client = _make_client()
        wl1 = _make_watchlist(watchlist_id="wl-1", name="A")
        wl2 = _make_watchlist(watchlist_id="wl-2", name="B")
        client.get_watchlists.return_value = [wl1, wl2]

        result = get_watchlists(client)

        assert len(result) == 2
        assert result[0].name == "A"

    def test_get_watchlists_empty(self):
        from dashboard.watchlist_manager import get_watchlists

        client = _make_client()
        client.get_watchlists.return_value = []

        result = get_watchlists(client)

        assert result == []


# ===========================================================================
# 7. Quote fetching with TTL cache
# ===========================================================================


class TestQuoteFetch:
    def test_fetch_quotes_returns_dict_per_symbol(self):
        """Each symbol maps to a dict with price, change, change_pct, volume."""
        from dashboard.watchlist_manager import fetch_quotes

        with patch("dashboard.watchlist_manager._fetch_quotes_raw") as mock_raw:
            mock_raw.return_value = {
                "AAPL": {
                    "price": 185.50,
                    "change": 2.30,
                    "change_pct": 1.25,
                    "volume": 50_000_000,
                },
            }
            result = fetch_quotes(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["price"] == 185.50
        assert result["AAPL"]["volume"] == 50_000_000

    def test_fetch_quotes_cache_hit(self):
        """Second call within TTL returns cached data without re-fetching."""
        from dashboard.watchlist_manager import _quote_cache, fetch_quotes

        _quote_cache.clear()

        with patch("dashboard.watchlist_manager._fetch_quotes_raw") as mock_raw:
            mock_raw.return_value = {
                "AAPL": {"price": 185.0, "change": 0, "change_pct": 0, "volume": 1},
            }
            fetch_quotes(["AAPL"], ttl_seconds=60)
            fetch_quotes(["AAPL"], ttl_seconds=60)

        # Only called once — second call used cache
        assert mock_raw.call_count == 1

    def test_fetch_quotes_cache_miss_after_expiry(self):
        """After TTL expires, data is re-fetched."""
        from dashboard.watchlist_manager import _quote_cache, fetch_quotes

        _quote_cache.clear()

        with patch("dashboard.watchlist_manager._fetch_quotes_raw") as mock_raw:
            mock_raw.return_value = {
                "AAPL": {"price": 185.0, "change": 0, "change_pct": 0, "volume": 1},
            }
            fetch_quotes(["AAPL"], ttl_seconds=0)  # immediate expiry
            time.sleep(0.01)
            fetch_quotes(["AAPL"], ttl_seconds=0)

        assert mock_raw.call_count == 2

    def test_fetch_quotes_empty_symbols(self):
        """Empty symbol list returns empty dict without calling API."""
        from dashboard.watchlist_manager import fetch_quotes

        result = fetch_quotes([])
        assert result == {}

    def test_fetch_quotes_partial_failure_returns_available(self):
        """If some symbols fail, available quotes are still returned."""
        from dashboard.watchlist_manager import _quote_cache, fetch_quotes

        _quote_cache.clear()

        with patch("dashboard.watchlist_manager._fetch_quotes_raw") as mock_raw:
            # Only AAPL succeeds; ZZZZ not in response
            mock_raw.return_value = {
                "AAPL": {"price": 185.0, "change": 0, "change_pct": 0, "volume": 1},
            }
            result = fetch_quotes(["AAPL", "ZZZZ"], ttl_seconds=0)

        assert "AAPL" in result
        assert "ZZZZ" not in result


# ===========================================================================
# 8. API error states
# ===========================================================================


class TestAPIErrors:
    def test_auth_failure_propagates(self):
        """401 errors from the client propagate for the UI layer to handle."""
        from alpaca.common.exceptions import APIError

        from dashboard.watchlist_manager import get_watchlists

        client = _make_client()
        client.get_watchlists.side_effect = APIError("unauthorized", 401)

        with pytest.raises(APIError):
            get_watchlists(client)

    def test_rate_limit_propagates(self):
        from alpaca.common.exceptions import APIError

        from dashboard.watchlist_manager import get_watchlists

        client = _make_client()
        client.get_watchlists.side_effect = APIError("rate limit", 429)

        with pytest.raises(APIError):
            get_watchlists(client)

    def test_timeout_propagates(self):
        from requests.exceptions import Timeout

        from dashboard.watchlist_manager import get_watchlists

        client = _make_client()
        client.get_watchlists.side_effect = Timeout("timed out")

        with pytest.raises(Timeout):
            get_watchlists(client)
