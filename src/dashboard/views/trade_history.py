"""Trade History tab — executed paper trades with details and timeline.

Shows filled orders from the Alpaca paper trading API with filtering,
volume metrics, and a timeline chart.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.charts import ACCENT_GREEN, ACCENT_RED, get_chart_template
from dashboard.data import fetch_closed_orders
from dashboard.fmt import fmt_currency, fmt_currency_short, fmt_date, fmt_datetime

# ── Render ───────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Trade History tab."""
    st.header("Trade History")

    try:
        orders = fetch_closed_orders()
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    if not orders:
        st.info("No completed trades yet. Filled orders will appear here.")
        return

    # ── Compute metrics ──────────────────────────────────────────────
    buys = [o for o in orders if o["side"] == "buy"]
    sells = [o for o in orders if o["side"] == "sell"]
    buy_volume = sum(o["qty"] * (o["filled_avg_price"] or 0) for o in buys)
    sell_volume = sum(o["qty"] * (o["filled_avg_price"] or 0) for o in sells)
    latest = orders[0]["filled_at"] if orders else None

    # ── Hero metrics ─────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Trades", len(orders))
    with c2:
        st.metric("Buy Volume", fmt_currency_short(buy_volume))
    with c3:
        st.metric("Sell Volume", fmt_currency_short(sell_volume))
    with c4:
        st.metric("Most Recent", fmt_date(latest))

    st.divider()

    # ── Filters ──────────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        symbol_filter = (
            st.text_input("Filter by symbol", key="th_symbol_filter", placeholder="e.g. AAPL")
            .strip()
            .upper()
        )
    with fc2:
        side_filter = st.selectbox("Side", ["ALL", "BUY", "SELL"], key="th_side_filter")
    with fc3:
        max_display = st.slider("Max rows", 10, 500, 100, key="th_max_rows")

    # Apply filters
    filtered = orders
    if symbol_filter:
        filtered = [o for o in filtered if symbol_filter in o["symbol"]]
    if side_filter != "ALL":
        filtered = [o for o in filtered if o["side"] == side_filter.lower()]
    filtered = filtered[:max_display]

    if not filtered:
        st.info("No trades match the current filters.")
        return

    # ── Trade table ──────────────────────────────────────────────────
    rows = []
    for o in filtered:
        total = o["qty"] * (o["filled_avg_price"] or 0)
        rows.append(
            {
                "Date": fmt_datetime(o["filled_at"]),
                "Symbol": o["symbol"],
                "Side": o["side"].upper(),
                "Qty": o["qty"],
                "Type": o["order_type"].upper(),
                "Fill Price": fmt_currency(o["filled_avg_price"], decimals=2)
                if o["filled_avg_price"]
                else "\u2014",
                "Total": fmt_currency(total, decimals=2),
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

    # ── Export ────────────────────────────────────────────────────────
    from dashboard.fmt import render_export_button

    render_export_button(df, "trade_history", label="Download CSV")

    # ── Timeline chart ───────────────────────────────────────────────
    st.subheader("Trade Timeline")

    fig = go.Figure()
    for side_val, color, label in [
        ("buy", ACCENT_GREEN, "BUY"),
        ("sell", ACCENT_RED, "SELL"),
    ]:
        side_orders = [o for o in filtered if o["side"] == side_val and o["filled_at"]]
        if side_orders:
            fig.add_trace(
                go.Scatter(
                    x=[o["filled_at"] for o in side_orders],
                    y=[o["symbol"] for o in side_orders],
                    mode="markers",
                    marker={"color": color, "size": 10},
                    name=label,
                    hovertemplate=(
                        f"<b>%{{y}}</b><br>%{{x|%b %d, %Y %I:%M %p}}<br>{label}<extra></extra>"
                    ),
                )
            )

    fig.update_layout(
        template=get_chart_template(),
        height=max(250, len(set(o["symbol"] for o in filtered)) * 40),
        xaxis_title="",
        yaxis_title="",
    )
    st.plotly_chart(fig, width="stretch")
