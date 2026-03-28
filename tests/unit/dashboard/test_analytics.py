"""Tests for dashboard.analytics — pure analytics logic.

Covers:
  - Risk metric calculations (known inputs → known outputs)
  - Trade journal creation from filled orders
  - Portfolio equity chart data processing
  - Edge cases: empty data, single trade, all wins, all losses, NaN/None
"""

from __future__ import annotations

import pytest

from dashboard.alpaca_models import OrderInfo

# ── RED PHASE 1: Risk metric calculations ────────────────────────────────

# Known trade P&L list for deterministic tests:
#   wins:  +100, +200, +50   (3 wins, total +350)
#   losses: -80, -120         (2 losses, total -200)
_PNL_LIST = [100.0, -80.0, 200.0, -120.0, 50.0]


class TestWinRate:
    def test_basic(self):
        from dashboard.analytics import compute_win_rate

        # 3 wins out of 5 trades = 60%
        assert compute_win_rate(_PNL_LIST) == pytest.approx(60.0)

    def test_all_wins(self):
        from dashboard.analytics import compute_win_rate

        assert compute_win_rate([10.0, 20.0, 30.0]) == pytest.approx(100.0)

    def test_all_losses(self):
        from dashboard.analytics import compute_win_rate

        assert compute_win_rate([-10.0, -20.0]) == pytest.approx(0.0)

    def test_empty(self):
        from dashboard.analytics import compute_win_rate

        assert compute_win_rate([]) == 0.0

    def test_breakeven_counted_as_win(self):
        from dashboard.analytics import compute_win_rate

        # Zero P&L is not a win (strictly > 0)
        assert compute_win_rate([0.0, 100.0]) == pytest.approx(50.0)


class TestAvgWinLoss:
    def test_avg_win(self):
        from dashboard.analytics import compute_avg_win

        # (100 + 200 + 50) / 3 = 116.67
        assert compute_avg_win(_PNL_LIST) == pytest.approx(116.6667, rel=1e-3)

    def test_avg_loss(self):
        from dashboard.analytics import compute_avg_loss

        # (80 + 120) / 2 = 100  (returned as positive magnitude)
        assert compute_avg_loss(_PNL_LIST) == pytest.approx(100.0)

    def test_avg_win_no_wins(self):
        from dashboard.analytics import compute_avg_win

        assert compute_avg_win([-10.0, -20.0]) == 0.0

    def test_avg_loss_no_losses(self):
        from dashboard.analytics import compute_avg_loss

        assert compute_avg_loss([10.0, 20.0]) == 0.0

    def test_empty(self):
        from dashboard.analytics import compute_avg_loss, compute_avg_win

        assert compute_avg_win([]) == 0.0
        assert compute_avg_loss([]) == 0.0


class TestMaxDrawdown:
    def test_basic(self):
        from dashboard.analytics import compute_max_drawdown

        # Equity curve: 100, 120, 90, 150, 130
        # Peak at 120, trough at 90 → drawdown = (120-90)/120 = 25%
        equity = [100.0, 120.0, 90.0, 150.0, 130.0]
        assert compute_max_drawdown(equity) == pytest.approx(25.0)

    def test_monotonic_up(self):
        from dashboard.analytics import compute_max_drawdown

        assert compute_max_drawdown([100.0, 110.0, 120.0]) == pytest.approx(0.0)

    def test_monotonic_down(self):
        from dashboard.analytics import compute_max_drawdown

        # 100 → 50 = 50% drawdown
        assert compute_max_drawdown([100.0, 80.0, 50.0]) == pytest.approx(50.0)

    def test_empty(self):
        from dashboard.analytics import compute_max_drawdown

        assert compute_max_drawdown([]) == 0.0

    def test_single_point(self):
        from dashboard.analytics import compute_max_drawdown

        assert compute_max_drawdown([100.0]) == 0.0


class TestProfitFactor:
    def test_basic(self):
        from dashboard.analytics import compute_profit_factor

        # gross profit = 350, gross loss = 200 → 1.75
        assert compute_profit_factor(_PNL_LIST) == pytest.approx(1.75)

    def test_no_losses(self):
        from dashboard.analytics import compute_profit_factor

        # Infinite profit factor → return inf
        assert compute_profit_factor([100.0, 200.0]) == float("inf")

    def test_no_wins(self):
        from dashboard.analytics import compute_profit_factor

        assert compute_profit_factor([-50.0, -100.0]) == pytest.approx(0.0)

    def test_empty(self):
        from dashboard.analytics import compute_profit_factor

        assert compute_profit_factor([]) == 0.0


class TestSharpeRatio:
    def test_basic(self):
        from dashboard.analytics import compute_sharpe_ratio

        # Known returns: mean=30, std≈known value
        returns = [100.0, -80.0, 200.0, -120.0, 50.0]
        result = compute_sharpe_ratio(returns)
        # mean = 30, std ≈ 117.30, sharpe ≈ 0.2557
        assert result == pytest.approx(0.2557, abs=0.01)

    def test_zero_std(self):
        from dashboard.analytics import compute_sharpe_ratio

        # All same return → std=0 → return 0
        assert compute_sharpe_ratio([10.0, 10.0, 10.0]) == 0.0

    def test_empty(self):
        from dashboard.analytics import compute_sharpe_ratio

        assert compute_sharpe_ratio([]) == 0.0

    def test_single_return(self):
        from dashboard.analytics import compute_sharpe_ratio

        assert compute_sharpe_ratio([50.0]) == 0.0


# ── RED PHASE 2: Trade journal creation ──────────────────────────────────

_FILLED_ORDER_BUY = OrderInfo(
    order_id="o1",
    symbol="AAPL",
    qty=10.0,
    side="buy",
    order_type="market",
    time_in_force="day",
    status="filled",
    created_at="2026-03-10T10:00:00+00:00",
    filled_at="2026-03-10T10:00:05+00:00",
    filled_avg_price=150.0,
)

_FILLED_ORDER_SELL = OrderInfo(
    order_id="o2",
    symbol="AAPL",
    qty=10.0,
    side="sell",
    order_type="market",
    time_in_force="day",
    status="filled",
    created_at="2026-03-15T14:00:00+00:00",
    filled_at="2026-03-15T14:00:03+00:00",
    filled_avg_price=160.0,
)

_FILLED_ORDER_LOSS = OrderInfo(
    order_id="o3",
    symbol="MSFT",
    qty=5.0,
    side="buy",
    order_type="limit",
    time_in_force="gtc",
    status="filled",
    created_at="2026-03-12T09:30:00+00:00",
    filled_at="2026-03-12T09:30:02+00:00",
    filled_avg_price=400.0,
)

_FILLED_ORDER_LOSS_EXIT = OrderInfo(
    order_id="o4",
    symbol="MSFT",
    qty=5.0,
    side="sell",
    order_type="market",
    time_in_force="day",
    status="filled",
    created_at="2026-03-18T11:00:00+00:00",
    filled_at="2026-03-18T11:00:01+00:00",
    filled_avg_price=380.0,
)


class TestBuildJournalEntries:
    def test_round_trip_trade(self):
        from dashboard.analytics import build_journal_entries

        entries = build_journal_entries([_FILLED_ORDER_BUY, _FILLED_ORDER_SELL])
        assert len(entries) == 1
        e = entries[0]
        assert e["symbol"] == "AAPL"
        assert e["side"] == "buy"
        assert e["qty"] == 10.0
        assert e["entry_price"] == 150.0
        assert e["exit_price"] == 160.0
        assert e["pnl"] == pytest.approx(100.0)  # (160-150)*10

    def test_losing_trade(self):
        from dashboard.analytics import build_journal_entries

        entries = build_journal_entries([_FILLED_ORDER_LOSS, _FILLED_ORDER_LOSS_EXIT])
        assert len(entries) == 1
        assert entries[0]["pnl"] == pytest.approx(-100.0)  # (380-400)*5

    def test_multiple_symbols(self):
        from dashboard.analytics import build_journal_entries

        entries = build_journal_entries(
            [_FILLED_ORDER_BUY, _FILLED_ORDER_SELL, _FILLED_ORDER_LOSS, _FILLED_ORDER_LOSS_EXIT]
        )
        assert len(entries) == 2
        symbols = {e["symbol"] for e in entries}
        assert symbols == {"AAPL", "MSFT"}

    def test_empty_orders(self):
        from dashboard.analytics import build_journal_entries

        assert build_journal_entries([]) == []

    def test_open_position_no_exit(self):
        from dashboard.analytics import build_journal_entries

        # Buy with no matching sell → no closed journal entry
        entries = build_journal_entries([_FILLED_ORDER_BUY])
        assert len(entries) == 0

    def test_order_missing_fill_price(self):
        from dashboard.analytics import build_journal_entries

        unfilled = OrderInfo(
            order_id="o99",
            symbol="GOOG",
            qty=1.0,
            side="buy",
            order_type="market",
            time_in_force="day",
            status="filled",
            created_at="2026-03-10T10:00:00+00:00",
            filled_at=None,
            filled_avg_price=None,
        )
        entries = build_journal_entries([unfilled])
        assert len(entries) == 0


# ── RED PHASE 3: Portfolio history / equity chart data ───────────────────


class TestPrepareEquityChartData:
    def test_normal_history(self):
        from dashboard.analytics import PortfolioHistory, prepare_equity_chart_data

        history = PortfolioHistory(
            timestamps=[1711100000, 1711186400, 1711272800],
            equity=[100000.0, 101000.0, 99500.0],
            profit_loss_pct=[0.0, 1.0, -0.5],
            base_value=100000.0,
        )
        df = prepare_equity_chart_data(history)
        assert len(df) == 3
        assert list(df.columns) == ["date", "equity", "pct_change"]
        assert df["equity"].tolist() == [100000.0, 101000.0, 99500.0]

    def test_empty_history(self):
        from dashboard.analytics import PortfolioHistory, prepare_equity_chart_data

        history = PortfolioHistory(timestamps=[], equity=[], profit_loss_pct=[], base_value=0.0)
        df = prepare_equity_chart_data(history)
        assert len(df) == 0

    def test_single_point(self):
        from dashboard.analytics import PortfolioHistory, prepare_equity_chart_data

        history = PortfolioHistory(
            timestamps=[1711100000],
            equity=[100000.0],
            profit_loss_pct=[0.0],
            base_value=100000.0,
        )
        df = prepare_equity_chart_data(history)
        assert len(df) == 1


# ── Edge case: compute_all_metrics integration ───────────────────────────


class TestComputeAllMetrics:
    def test_with_trades(self):
        from dashboard.analytics import compute_all_metrics

        pnl = _PNL_LIST
        equity = [100000.0, 100100.0, 100020.0, 100220.0, 100100.0, 100150.0]
        metrics = compute_all_metrics(pnl, equity)
        assert metrics["win_rate"] == pytest.approx(60.0)
        assert metrics["avg_win"] == pytest.approx(116.6667, rel=1e-3)
        assert metrics["avg_loss"] == pytest.approx(100.0)
        assert metrics["profit_factor"] == pytest.approx(1.75)
        assert "max_drawdown" in metrics
        assert "sharpe_ratio" in metrics

    def test_no_trades(self):
        from dashboard.analytics import compute_all_metrics

        metrics = compute_all_metrics([], [])
        assert metrics["win_rate"] == 0.0
        assert metrics["avg_win"] == 0.0
        assert metrics["avg_loss"] == 0.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["max_drawdown"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0
