"""Account Summary tab — paper trading account health at a glance.

Displays equity, cash, buying power, day's P&L, and an equity-over-time
chart sourced from the Alpaca paper trading API.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.alpaca_session import get_alpaca_client
from dashboard.analytics import PortfolioHistory, prepare_equity_chart_data
from dashboard.charts import ACCENT_BLUE, get_chart_template
from dashboard.fmt import fmt_currency, fmt_currency_short, fmt_delta_currency

# ── Data fetching (cached) ───────────────────────────────────────────────


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_account() -> dict:
    client = get_alpaca_client()
    acct = client.get_account()
    return {
        "account_id": acct.account_id,
        "equity": acct.equity,
        "cash": acct.cash,
        "buying_power": acct.buying_power,
        "portfolio_value": acct.portfolio_value,
        "pattern_day_trader": acct.pattern_day_trader,
        "trading_blocked": acct.trading_blocked,
        "account_blocked": acct.account_blocked,
        "daytrade_count": acct.daytrade_count,
        "currency": acct.currency,
        "status": acct.status,
    }


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_equity_history(period: str, timeframe: str) -> dict | None:
    client = get_alpaca_client()
    try:
        h = client.get_portfolio_history(period=period, timeframe=timeframe)
        return {
            "timestamps": h.timestamps,
            "equity": h.equity,
            "profit_loss_pct": h.profit_loss_pct,
            "base_value": h.base_value,
        }
    except Exception:
        return None


# ── Render ───────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Account Summary tab."""
    st.header("Account Summary")

    try:
        acct = _fetch_account()
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    # ── Hero metrics ─────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Equity", fmt_currency_short(acct["equity"]))
    with c2:
        st.metric("Cash Balance", fmt_currency_short(acct["cash"]))
    with c3:
        st.metric("Buying Power", fmt_currency_short(acct["buying_power"]))
    with c4:
        st.metric("Portfolio Value", fmt_currency_short(acct["portfolio_value"]))

    st.divider()

    # ── Equity curve ─────────────────────────────────────────────────
    st.subheader("Equity Over Time")

    period_map = {
        "1 Day": ("1D", "5Min"),
        "1 Week": ("1W", "15Min"),
        "1 Month": ("1M", "1D"),
        "3 Months": ("3M", "1D"),
        "1 Year": ("1A", "1D"),
        "All Time": ("all", "1D"),
    }

    selected = st.radio(
        "Period",
        list(period_map.keys()),
        horizontal=True,
        key="acct_period",
        label_visibility="collapsed",
    )

    period, timeframe = period_map[selected]
    raw = _fetch_equity_history(period, timeframe)

    if raw is None or not raw["timestamps"]:
        st.info("No portfolio history available for this period.")
    else:
        history = PortfolioHistory(
            timestamps=raw["timestamps"],
            equity=raw["equity"],
            profit_loss_pct=raw["profit_loss_pct"],
            base_value=raw["base_value"],
        )
        df = prepare_equity_chart_data(history)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["equity"],
                mode="lines",
                line={"color": ACCENT_BLUE, "width": 2},
                fill="tozeroy",
                fillcolor="rgba(108,158,255,0.10)",
                hovertemplate=(
                    "<b>%{x|%b %d, %Y %I:%M %p}</b><br>Equity: $%{y:,.2f}<extra></extra>"
                ),
            )
        )
        fig.update_layout(
            template=get_chart_template(),
            yaxis_title="Equity ($)",
            xaxis_title="",
            height=380,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Day P&L from equity curve
        if len(history.equity) >= 2:
            day_pnl = history.equity[-1] - history.equity[0]
            st.metric(
                f"P&L ({selected})",
                fmt_currency(day_pnl, decimals=2),
                delta=fmt_delta_currency(day_pnl, decimals=2),
            )

    # ── Account details ──────────────────────────────────────────────
    with st.expander("Account Details"):
        det1, det2, det3 = st.columns(3)
        with det1:
            st.metric("Account ID", acct["account_id"][:12] + "...")
            st.metric("Status", acct["status"])
        with det2:
            st.metric("Currency", acct["currency"])
            pdt = "Yes" if acct["pattern_day_trader"] else "No"
            st.metric("Pattern Day Trader", pdt)
        with det3:
            st.metric("Daytrade Count", acct["daytrade_count"])
            blocked = "Yes" if acct["trading_blocked"] else "No"
            st.metric("Trading Blocked", blocked)
