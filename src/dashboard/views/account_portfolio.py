"""Account overview and portfolio positions for Alpaca paper trading.

Renders hero metrics (equity, buying power, cash, status) and a
positions table with per-row close buttons.  All data comes from the
foundation ``AlpacaClient``; errors go through ``handle_alpaca_error``.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.fmt import fmt_currency_short

# ── Account Overview ──────────────────────────────────────────────────────


def render_account_overview(client: AlpacaClient) -> None:
    """Render hero metrics for the paper-trading account."""
    try:
        account = client.get_account()
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    st.subheader("Account Overview")

    col1, col2, col3, col4, col5 = st.columns(5, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric("Equity", fmt_currency_short(account.equity))
    with col2:
        st.metric("Buying Power", fmt_currency_short(account.buying_power))
    with col3:
        st.metric("Cash", fmt_currency_short(account.cash))
    with col4:
        st.metric("Day Trades", account.daytrade_count)
    with col5:
        st.metric("Status", account.status)

    # Warnings and errors
    if account.pattern_day_trader:
        st.warning(
            f"PDT flag active — {account.daytrade_count} day trades in the "
            "last 5 business days. A 4th day trade will be rejected if "
            "equity is under $25K."
        )

    if account.trading_blocked:
        st.error("Trading is blocked on this account. Contact Alpaca support.")

    if account.account_blocked:
        st.error("Account is blocked. Contact Alpaca support.")


# ── Positions Table ───────────────────────────────────────────────────────


def render_positions_table(client: AlpacaClient) -> None:
    """Render the portfolio positions table with close buttons."""
    try:
        positions = client.get_positions()
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    st.subheader("Positions")

    if not positions:
        st.info("No open positions. Use the order entry to open your first paper trade.")
        return

    # Build DataFrame
    rows = []
    for p in positions:
        rows.append(
            {
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Side": p.side,
                "Avg Entry": p.avg_entry_price,
                "Current Price": p.current_price,
                "Market Value": p.market_value,
                "Unrealized P&L": p.unrealized_pl,
                "P&L %": p.unrealized_plpc * 100,
            }
        )

    df = pd.DataFrame(rows)

    column_config = {
        "Symbol": st.column_config.TextColumn("Symbol", width="small"),
        "Qty": st.column_config.NumberColumn("Qty", format="%.2f"),
        "Side": st.column_config.TextColumn("Side", width="small"),
        "Avg Entry": st.column_config.NumberColumn("Avg Entry", format="$%.2f"),
        "Current Price": st.column_config.NumberColumn("Current Price", format="$%.2f"),
        "Market Value": st.column_config.NumberColumn("Market Value", format="$%,.2f"),
        "Unrealized P&L": st.column_config.NumberColumn("Unrealized P&L", format="$%+,.2f"),
        "P&L %": st.column_config.NumberColumn("P&L %", format="%+.2f%%"),
    }

    st.dataframe(
        df,
        column_config=column_config,
        width="stretch",
        hide_index=True,
        height=min(len(rows) * 35 + 38, 400),
    )

    # Close-position buttons
    st.caption("Close a position:")
    cols = st.columns(min(len(positions), 4))
    for i, p in enumerate(positions):
        with cols[i % len(cols)]:
            if st.button(f"Close {p.symbol}", key=f"close_{p.symbol}"):
                _handle_close_position(client, p.symbol, st)


# ── Close Position Handler ────────────────────────────────────────────────


def _handle_close_position(client: AlpacaClient, symbol: str, _st=None) -> None:
    """Close a position and show success/error feedback.

    The *_st* parameter allows tests to inject a mock Streamlit module.
    """
    ui = _st or st
    try:
        order = client.close_position(symbol)
        ui.success(f"Close order submitted for {symbol} (order {order.order_id}).")
        ui.rerun()
    except Exception as exc:
        handle_alpaca_error(exc)
