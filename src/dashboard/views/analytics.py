"""Analytics sub-view for the Paper Trading page.

Renders three sections:
  1. Portfolio performance chart (equity over time)
  2. Risk metrics card
  3. Trade journal table
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.analytics import (
    PortfolioHistory,
    build_journal_entries,
    compute_all_metrics,
    prepare_equity_chart_data,
)
from dashboard.charts import ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED
from dashboard.fmt import fmt_currency, fmt_pct

# ── Session-state key for editable journal notes ─────────────────────────
_NOTES_KEY = "analytics_journal_notes"


def _fetch_portfolio_history(
    client: AlpacaClient, period: str, timeframe: str
) -> PortfolioHistory | None:
    """Call the Alpaca portfolio history endpoint.

    The foundation client does not yet expose ``get_portfolio_history()``,
    so we call the underlying SDK directly as a temporary bridge.  Once the
    foundation adds the method, this should be replaced.
    """
    try:
        raw = client._client.get_portfolio_history(period=period, timeframe=timeframe)
        return PortfolioHistory(
            timestamps=list(raw.timestamp or []),
            equity=[float(e) for e in (raw.equity or [])],
            profit_loss_pct=[float(p) for p in (raw.profit_loss_pct or [])],
            base_value=float(raw.base_value) if raw.base_value else 0.0,
        )
    except Exception as exc:
        handle_alpaca_error(exc)
        return None


# ── Section renderers ────────────────────────────────────────────────────


def _render_performance_chart(client: AlpacaClient) -> list[float]:
    """Render the equity-over-time chart.  Returns the equity curve list."""
    st.subheader("Portfolio Performance")

    period_map = {
        "1 Day": "1D",
        "1 Week": "1W",
        "1 Month": "1M",
        "3 Months": "3M",
        "1 Year": "1A",
        "All Time": "all",
    }
    tf_map = {
        "1 Day": "5Min",
        "1 Week": "15Min",
        "1 Month": "1D",
        "3 Months": "1D",
        "1 Year": "1D",
        "All Time": "1D",
    }

    selected = st.radio(
        "Period",
        list(period_map.keys()),
        horizontal=True,
        key="analytics_period",
        label_visibility="collapsed",
    )

    history = _fetch_portfolio_history(client, period_map[selected], tf_map[selected])
    if history is None or not history.timestamps:
        st.info("No portfolio history available for this period.")
        return []

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
            hovertemplate="<b>%{x|%b %d, %Y %I:%M %p}</b><br>Equity: $%{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        yaxis_title="Equity ($)",
        xaxis_title="",
        height=380,
    )
    st.plotly_chart(fig, width="stretch")
    return history.equity


def _render_risk_metrics(pnl_list: list[float], equity_curve: list[float]) -> None:
    """Render the risk metrics card."""
    st.subheader("Risk Metrics")

    if not pnl_list:
        st.info("Complete some trades to see risk metrics.")
        return

    m = compute_all_metrics(pnl_list, equity_curve)

    c1, c2, c3, c4, c5, c6 = st.columns(6, gap="medium")
    with c1:
        st.metric("Win Rate", fmt_pct(m["win_rate"]))
    with c2:
        st.metric("Avg Win", fmt_currency(m["avg_win"], decimals=2))
    with c3:
        st.metric("Avg Loss", fmt_currency(m["avg_loss"], decimals=2))
    with c4:
        st.metric("Max Drawdown", fmt_pct(m["max_drawdown"]))
    with c5:
        pf = m["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        st.metric("Profit Factor", pf_str)
    with c6:
        st.metric("Sharpe Ratio", f"{m['sharpe_ratio']:.2f}")


def _render_trade_journal(client: AlpacaClient) -> list[float]:
    """Render the trade journal table.  Returns the P&L list for metrics."""
    st.subheader("Trade Journal")

    try:
        filled = client.get_orders(status="closed", limit=500)
    except Exception as exc:
        handle_alpaca_error(exc)
        return []

    entries = build_journal_entries(filled)

    if not entries:
        st.info("No closed trades yet.  Completed round-trip trades will appear here.")
        return []

    # Initialise notes in session state
    if _NOTES_KEY not in st.session_state:
        st.session_state[_NOTES_KEY] = {}

    for i, entry in enumerate(entries):
        key = f"{entry['symbol']}_{entry['entry_date']}"
        entry["notes"] = st.session_state[_NOTES_KEY].get(key, "")

    # Display as editable table
    import pandas as pd

    df = pd.DataFrame(entries)
    display_cols = [
        "symbol",
        "side",
        "qty",
        "entry_price",
        "exit_price",
        "entry_date",
        "exit_date",
        "pnl",
        "notes",
    ]
    df = df[[c for c in display_cols if c in df.columns]]

    # Colour P&L column
    def _color_pnl(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return f"color: {ACCENT_GREEN}"
            if val < 0:
                return f"color: {ACCENT_RED}"
        return ""

    styled = df.style.applymap(_color_pnl, subset=["pnl"] if "pnl" in df.columns else [])
    st.dataframe(styled, width="stretch", hide_index=True)

    # Editable notes per entry
    with st.expander("Edit trade notes"):
        for i, entry in enumerate(entries):
            key = f"{entry['symbol']}_{entry['entry_date']}"
            note = st.text_input(
                f"{entry['symbol']} ({entry['entry_date'][:10]})",
                value=st.session_state[_NOTES_KEY].get(key, ""),
                key=f"note_{i}",
            )
            st.session_state[_NOTES_KEY][key] = note

    return [e["pnl"] for e in entries]


# ── Main render ──────────────────────────────────────────────────────────


def render_analytics(client: AlpacaClient) -> None:
    """Render the full analytics section."""
    equity_curve = _render_performance_chart(client)
    st.divider()
    pnl_list = _render_trade_journal(client)
    st.divider()
    _render_risk_metrics(pnl_list, equity_curve)
