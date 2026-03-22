"""
Unit tests for portfolio execution handler.

Coverage:
  - _split_tranches: tranche splitting logic
  - AlpacaClient.submit_order: limit-only validation
  - handler: live trading guard (dev environment refuses api.alpaca.markets)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from portfolio.execution.handler import _split_tranches

# ------------------------------------------------------------------ #
# TestSplitTranches                                                    #
# ------------------------------------------------------------------ #


class TestSplitTranches:
    """Pure Python — no mocks needed."""

    def test_small_order_single_tranche(self):
        # $5K < $10K threshold → no splitting
        tranches = _split_tranches(50.0, 5_000.0)
        assert tranches == [50.0]

    def test_large_order_splits_into_3_to_5(self):
        # $30K, 100 shares → should split into 3–5 tranches
        tranches = _split_tranches(100.0, 30_000.0)
        assert 3 <= len(tranches) <= 5

    def test_tranche_quantities_sum_to_total(self):
        total_qty = 100.0
        tranches = _split_tranches(total_qty, 30_000.0)
        assert sum(tranches) == pytest.approx(total_qty)

    def test_zero_qty_returns_single_zero(self):
        tranches = _split_tranches(0.0, 50_000.0)
        assert tranches == [0.0]

    def test_qty_less_than_n_no_split(self):
        # qty=2 is less than the minimum 3 tranches; cannot split
        tranches = _split_tranches(2.0, 30_000.0)
        assert len(tranches) == 1

    def test_tranches_are_whole_numbers_for_integer_qty(self):
        tranches = _split_tranches(99.0, 30_000.0)
        for t in tranches:
            assert t == int(t)


# ------------------------------------------------------------------ #
# TestSubmitOrderValidation                                            #
# ------------------------------------------------------------------ #


class TestSubmitOrderValidation:
    """Validation fires before any HTTP call — no real network needed."""

    def _make_client(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        from portfolio.execution.handler import AlpacaClient

        return AlpacaClient()

    def test_market_order_raises(self, monkeypatch):
        client = self._make_client(monkeypatch)
        with pytest.raises(ValueError, match="limit"):
            client.submit_order("AAPL", 10, "buy", order_type="market", limit_price=100.0)

    def test_missing_limit_price_raises(self, monkeypatch):
        client = self._make_client(monkeypatch)
        with pytest.raises(ValueError, match="limit"):
            client.submit_order("AAPL", 10, "buy", order_type="limit", limit_price=None)


# ------------------------------------------------------------------ #
# TestLiveTradingGuard                                                 #
# ------------------------------------------------------------------ #


class TestLiveTradingGuard:
    """The handler must refuse live-trading URLs outside of prod."""

    def test_live_url_refused_in_dev(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        from portfolio.execution.handler import handler

        result = handler({}, None)

        assert any("Live trading refused" in e for e in result["errors"])
        assert result["orders_submitted"] == []

    def test_guard_only_fires_for_alpaca_markets_url(self, monkeypatch):
        # A non-alpaca URL (e.g. local mock) must not trigger the live-trading guard.
        # Note: "api.alpaca.markets" is a substring of the paper URL too, so this
        # test uses a mock URL to prove the check is URL-conditional, not blanket.
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ALPACA_BASE_URL", "http://localhost:8080")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("portfolio.execution.handler.AlpacaClient") as mock_alpaca_cls,
            patch("portfolio.execution.handler.DynamoClient"),
        ):
            mock_alpaca_cls.return_value = MagicMock()
            from portfolio.execution.handler import handler

            result = handler({}, None)

        assert not any("Live trading refused" in e for e in result.get("errors", []))
        assert result["orders_submitted"] == []
