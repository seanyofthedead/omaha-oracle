"""
Unit tests for portfolio execution handler.

Coverage:
  - _split_tranches: tranche splitting logic
  - AlpacaClient.submit_order: limit-only validation
  - handler: live trading guard (dev environment refuses api.alpaca.markets)
  - Kill switch correct PK (bug #1)
  - Dedup sentinel correct PK attr (bug #2)
  - Guardrails called before BUY (bug #3)
  - Sector field not sourced from asset_class (bug #6)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from portfolio.execution.handler import _split_tranches
from shared.dynamo_client import DynamoClient

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


# ------------------------------------------------------------------ #
# Bug #1: Kill switch uses wrong PK                                    #
# ------------------------------------------------------------------ #


class TestKillSwitchCorrectKey:
    """The kill switch must query config table with config_key PK, not pk/sk."""

    def test_kill_switch_uses_config_key(self, monkeypatch):
        """_check_trading_enabled must use {'config_key': 'trading_enabled'} as the key."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        mock_client = MagicMock()
        mock_client.get_item.return_value = None  # trading enabled (no item)

        from portfolio.execution.handler import _check_trading_enabled

        _check_trading_enabled(mock_client)

        # Verify the key used to query
        mock_client.get_item.assert_called_once_with({"config_key": "trading_enabled"})

    def test_kill_switch_disabled_raises(self, monkeypatch):
        """When trading_enabled is False, should raise RuntimeError."""
        monkeypatch.setenv("ENVIRONMENT", "dev")

        mock_client = MagicMock()
        mock_client.get_item.return_value = {"config_key": "trading_enabled", "value": False}

        from portfolio.execution.handler import _check_trading_enabled

        with pytest.raises(RuntimeError, match="kill switch"):
            _check_trading_enabled(mock_client)

    def test_allocation_kill_switch_uses_config_key(self, monkeypatch):
        """Allocation handler's _check_trading_enabled must also use config_key."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        mock_client = MagicMock()
        mock_client.get_item.return_value = None

        from portfolio.allocation.handler import _check_trading_enabled as alloc_check

        alloc_check(mock_client)
        mock_client.get_item.assert_called_once_with({"config_key": "trading_enabled"})


# ------------------------------------------------------------------ #
# Bug #2: Dedup sentinel wrong attribute                               #
# ------------------------------------------------------------------ #


class TestDedupSentinelCustomPk:
    """put_item_if_not_exists must support custom PK attribute names."""

    def test_put_item_if_not_exists_with_custom_pk_attr(
        self, aws_env, _moto_session, monkeypatch  # noqa: ARG002
    ):
        """When pk_attr='decision_id', the condition should use attribute_not_exists(decision_id)."""
        table_name = "omaha-oracle-dev-dedup-test"
        table = _moto_session.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "decision_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "decision_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        try:
            client = DynamoClient(table_name)
            item = {"decision_id": "DEDUP#AAPL#buy#2026-03-28", "ticker": "AAPL"}

            # First write should succeed
            assert client.put_item_if_not_exists(item, pk_attr="decision_id") is True
            # Second write should detect duplicate
            assert client.put_item_if_not_exists(item, pk_attr="decision_id") is False
        finally:
            table.delete()

    def test_put_item_if_not_exists_default_pk_still_works(
        self, aws_env, _moto_session, monkeypatch  # noqa: ARG002
    ):
        """Default pk_attr='pk' should still work for backwards compat."""
        table_name = "omaha-oracle-dev-dedup-default-test"
        table = _moto_session.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        try:
            client = DynamoClient(table_name)
            item = {"pk": "DEDUP#AAPL", "sk": "DEDUP", "ticker": "AAPL"}

            assert client.put_item_if_not_exists(item) is True
            assert client.put_item_if_not_exists(item) is False
        finally:
            table.delete()


# ------------------------------------------------------------------ #
# Bug #3: Guardrails not called from execution                        #
# ------------------------------------------------------------------ #


class TestGuardrailsCalledBeforeBuy:
    """Execution handler must call check_all_guardrails before submitting BUY orders."""

    def test_guardrails_block_buy_when_violated(self, monkeypatch):
        """If guardrails fail, the BUY order should not be submitted."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        monkeypatch.setenv("ALPACA_BASE_URL", "http://localhost:8080")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        mock_alpaca = MagicMock()
        mock_alpaca.get_account.return_value = {"portfolio_value": "100000", "cash": "50000"}
        mock_alpaca.get_positions.return_value = []

        mock_decisions_client = MagicMock()
        mock_decisions_client.put_item_if_not_exists.return_value = True  # not a duplicate

        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {},
        }

        import portfolio.execution.handler as exec_mod

        mock_guardrails = MagicMock(
            return_value={"passed": False, "violations": ["Position exceeds max 15%"]}
        )

        with (
            patch.object(exec_mod, "check_all_guardrails", mock_guardrails),
            patch.object(exec_mod, "_check_trading_enabled", lambda _: None),
            patch.object(exec_mod, "load_portfolio_state", lambda _: portfolio_state),
            patch.object(exec_mod, "_sync_portfolio_state", lambda *a, **kw: None),
            patch.object(exec_mod, "AlpacaClient", return_value=mock_alpaca),
            patch.object(exec_mod, "DynamoClient") as mock_dc_cls,
        ):
            mock_dc_cls.return_value = mock_decisions_client

            event = {
                "buy_decisions": [
                    {
                        "ticker": "AAPL",
                        "signal": "BUY",
                        "position_size_usd": 20_000,
                        "sector": "Technology",
                    }
                ]
            }
            # Handler raises RuntimeError when all orders fail; that's expected
            with pytest.raises(RuntimeError, match="guardrail"):
                exec_mod.handler(event, None)

        # Guardrails should have been called
        assert mock_guardrails.called
        # Order should NOT have been submitted since guardrails failed
        mock_alpaca.submit_order.assert_not_called()


# ------------------------------------------------------------------ #
# Bug #6: Sector field sourced from asset_class                        #
# ------------------------------------------------------------------ #


class TestSectorFieldCorrect:
    """_sync_portfolio_state must not write asset_class as sector."""

    def test_sync_does_not_use_asset_class_as_sector(self, monkeypatch):
        """The sector field in portfolio state should not be 'us_equity' from asset_class."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        mock_alpaca = MagicMock()
        mock_alpaca.get_account.return_value = {"portfolio_value": "100000", "cash": "50000"}
        mock_alpaca.get_positions.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "market_value": "15000",
                "cost_basis": "14000",
                "asset_class": "us_equity",
            }
        ]

        stored_items = []
        mock_portfolio_client = MagicMock()
        mock_portfolio_client.put_item.side_effect = lambda item: stored_items.append(item)

        with patch("portfolio.execution.handler.DynamoClient", return_value=mock_portfolio_client):
            from portfolio.execution.handler import _sync_portfolio_state

            _sync_portfolio_state(mock_alpaca, "omaha-oracle-dev-portfolio")

        # Find the position item (not the account summary)
        position_items = [i for i in stored_items if i.get("pk") == "POSITION"]
        assert len(position_items) == 1
        # sector should NOT be 'us_equity' — that's the asset_class, not sector
        assert position_items[0]["sector"] != "us_equity"
