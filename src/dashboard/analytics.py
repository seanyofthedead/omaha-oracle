"""Pure analytics logic for the Paper Trading analytics feature.

All functions are stateless and Streamlit-free so they can be unit-tested
without a running app.  The UI layer (``views/analytics.py``) calls these.

Metric definitions
------------------
- **Win rate**: % of trades with P&L > 0.
- **Avg win / avg loss**: Mean of winning / losing P&L values (loss as
  positive magnitude).
- **Max drawdown**: Largest peak-to-trough decline in equity, as %.
- **Profit factor**: Gross profit / gross loss.
- **Sharpe ratio**: mean(returns) / std(returns).  Annualisation is omitted
  because the trade journal produces irregular intervals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from dashboard.alpaca_models import OrderInfo

# ── Data models ──────────────────────────────────────────────────────────


@dataclass
class PortfolioHistory:
    """Equity-over-time snapshot returned by the Alpaca portfolio history
    endpoint.  The foundation client should return this; for now we define
    it here so the analytics layer can be built and tested independently.
    """

    timestamps: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    profit_loss_pct: list[float] = field(default_factory=list)
    base_value: float = 0.0


# ── Risk metric functions ────────────────────────────────────────────────


def compute_win_rate(pnl_list: list[float]) -> float:
    """Return win rate as a percentage (0-100).  A win is P&L > 0."""
    if not pnl_list:
        return 0.0
    wins = sum(1 for p in pnl_list if p > 0)
    return (wins / len(pnl_list)) * 100.0


def compute_avg_win(pnl_list: list[float]) -> float:
    """Return the average winning trade P&L.  Returns 0 if no wins."""
    wins = [p for p in pnl_list if p > 0]
    return sum(wins) / len(wins) if wins else 0.0


def compute_avg_loss(pnl_list: list[float]) -> float:
    """Return the average losing trade magnitude (positive).  0 if no losses."""
    losses = [abs(p) for p in pnl_list if p < 0]
    return sum(losses) / len(losses) if losses else 0.0


def compute_max_drawdown(equity_curve: list[float]) -> float:
    """Return max drawdown as a percentage (0-100).

    Scans the equity curve for the largest peak-to-trough decline.
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_profit_factor(pnl_list: list[float]) -> float:
    """Return gross profit / gross loss.  inf if no losses, 0 if no wins."""
    gross_profit = sum(p for p in pnl_list if p > 0)
    gross_loss = sum(abs(p) for p in pnl_list if p < 0)
    if not pnl_list:
        return 0.0
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_sharpe_ratio(returns: list[float]) -> float:
    """Return Sharpe ratio (non-annualised): mean / std.

    Returns 0 when there are fewer than 2 data points or std is zero.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return mean / std


def compute_all_metrics(pnl_list: list[float], equity_curve: list[float]) -> dict[str, float]:
    """Compute all risk metrics in one call.  Convenience for the UI."""
    return {
        "win_rate": compute_win_rate(pnl_list),
        "avg_win": compute_avg_win(pnl_list),
        "avg_loss": compute_avg_loss(pnl_list),
        "max_drawdown": compute_max_drawdown(equity_curve),
        "profit_factor": compute_profit_factor(pnl_list),
        "sharpe_ratio": compute_sharpe_ratio(pnl_list),
    }


# ── Trade journal builder ───────────────────────────────────────────────


def build_journal_entries(filled_orders: list[OrderInfo]) -> list[dict[str, Any]]:
    """Build closed-trade journal entries from a list of filled orders.

    Pairs buy → sell (or sell → buy for shorts) per symbol using FIFO.
    Only orders with a valid ``filled_avg_price`` are considered.
    Returns a list of dicts with keys: symbol, side, qty, entry_price,
    exit_price, entry_date, exit_date, pnl, notes.
    """
    # Filter to usable fills
    usable = [
        o for o in filled_orders if o.filled_avg_price is not None and o.filled_at is not None
    ]

    # Sort by fill time
    usable.sort(key=lambda o: o.filled_at or "")

    # FIFO matching per symbol
    open_legs: dict[str, list[OrderInfo]] = {}
    entries: list[dict[str, Any]] = []

    for order in usable:
        sym = order.symbol
        if sym not in open_legs:
            open_legs[sym] = []

        # Check if this order closes an existing open leg
        if open_legs[sym] and open_legs[sym][0].side != order.side:
            opener = open_legs[sym].pop(0)
            qty = min(opener.qty, order.qty)
            # Both filtered for non-None filled_avg_price above
            assert opener.filled_avg_price is not None
            assert order.filled_avg_price is not None

            if opener.side == "buy":
                pnl = (order.filled_avg_price - opener.filled_avg_price) * qty
            else:
                pnl = (opener.filled_avg_price - order.filled_avg_price) * qty

            entries.append(
                {
                    "symbol": sym,
                    "side": opener.side,
                    "qty": qty,
                    "entry_price": opener.filled_avg_price,
                    "exit_price": order.filled_avg_price,
                    "entry_date": opener.filled_at,
                    "exit_date": order.filled_at,
                    "pnl": pnl,
                    "notes": "",
                }
            )
        else:
            open_legs[sym].append(order)

    return entries


# ── Equity chart data ────────────────────────────────────────────────────


def prepare_equity_chart_data(history: PortfolioHistory) -> pd.DataFrame:
    """Convert a ``PortfolioHistory`` into a DataFrame ready for Plotly.

    Columns: date, equity, pct_change.
    """
    if not history.timestamps:
        return pd.DataFrame(columns=["date", "equity", "pct_change"])

    dates = [datetime.fromtimestamp(ts, tz=UTC) for ts in history.timestamps]
    return pd.DataFrame(
        {
            "date": dates,
            "equity": history.equity,
            "pct_change": history.profit_loss_pct,
        }
    )
