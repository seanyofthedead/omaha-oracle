"""Backtesting — replay historical decisions against actual prices."""

from __future__ import annotations

from datetime import UTC, datetime

import plotly.graph_objects as go
import streamlit as st

from dashboard.data import load_decisions


def render() -> None:
    st.header("Backtesting")
    st.caption(
        "Replay past decisions against actual market prices to evaluate system performance."
    )

    # Load all decisions
    with st.spinner("Loading decision history..."):
        decisions = load_decisions(limit=500)

    if not decisions:
        st.info(
            "No decisions found. Run the analysis pipeline to generate buy/sell signals first."
        )
        return

    # Date range selector in sidebar
    timestamps = [
        d.get("timestamp", "")[:10] for d in decisions if d.get("timestamp")
    ]
    if not timestamps:
        st.info("No timestamped decisions found.")
        return

    min_date = datetime.strptime(min(timestamps), "%Y-%m-%d").date()
    max_date = datetime.now(UTC).date()

    st.sidebar.subheader("Backtest Settings")
    date_range = st.sidebar.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
    else:
        start, end = min_date, max_date

    initial_capital = st.sidebar.number_input(
        "Initial capital ($)",
        min_value=10000,
        max_value=10000000,
        value=100000,
        step=10000,
    )

    # Filter decisions by date range
    filtered = [
        d
        for d in decisions
        if d.get("timestamp", "")[:10] >= str(start)
        and d.get("timestamp", "")[:10] <= str(end)
    ]

    if not filtered:
        st.info(f"No decisions found between {start} and {end}.")
        return

    # Run backtest
    from backtesting.engine import run_backtest

    with st.spinner("Running backtest simulation..."):
        # Cache in session state keyed by params
        cache_key = f"backtest_{start}_{end}_{initial_capital}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = run_backtest(
                filtered, initial_capital=float(initial_capital)
            )
        result = st.session_state[cache_key]

    if not result["dates"]:
        st.warning("Could not fetch price data for the selected period.")
        return

    metrics = result["metrics"]

    # Hero metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Return", f"{metrics['total_return_pct']:+.1f}%")
    c2.metric("S&P 500 Return", f"{metrics['spy_return_pct']:+.1f}%")
    c3.metric("Alpha", f"{metrics['alpha_pct']:+.1f}%")
    c4.metric("Max Drawdown", f"-{metrics['max_drawdown_pct']:.1f}%")

    c5, c6, c7, _ = st.columns(4)
    c5.metric("Win Rate", f"{metrics['win_rate_pct']:.0f}%")
    c6.metric("Total Trades", metrics["total_trades"])
    c7.metric("Open Positions", metrics["open_positions"])

    st.divider()

    # Equity curve chart
    tab_chart, tab_trades = st.tabs(["Equity Curve", "Trade Log"])

    with tab_chart:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=result["dates"],
                y=result["portfolio_values"],
                mode="lines",
                name="Portfolio",
                line=dict(color="#6C9EFF", width=2),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=result["dates"],
                y=result["spy_values"],
                mode="lines",
                name="S&P 500 (SPY)",
                line=dict(color="rgba(255,255,255,0.3)", width=1, dash="dash"),
            )
        )

        # Mark trades on chart
        buy_trades = [t for t in result["trades"] if t["signal"] == "BUY"]
        sell_trades = [t for t in result["trades"] if t["signal"] == "SELL"]

        if buy_trades:
            buy_dates = [t["date"] for t in buy_trades]
            buy_vals = []
            for bd in buy_dates:
                idx = (
                    result["dates"].index(bd) if bd in result["dates"] else -1
                )
                buy_vals.append(
                    result["portfolio_values"][idx] if idx >= 0 else None
                )
            fig.add_trace(
                go.Scatter(
                    x=buy_dates,
                    y=buy_vals,
                    mode="markers",
                    name="BUY",
                    marker=dict(
                        color="#4CAF50", size=10, symbol="triangle-up"
                    ),
                )
            )

        if sell_trades:
            sell_dates = [t["date"] for t in sell_trades]
            sell_vals = []
            for sd in sell_dates:
                idx = (
                    result["dates"].index(sd) if sd in result["dates"] else -1
                )
                sell_vals.append(
                    result["portfolio_values"][idx] if idx >= 0 else None
                )
            fig.add_trace(
                go.Scatter(
                    x=sell_dates,
                    y=sell_vals,
                    mode="markers",
                    name="SELL",
                    marker=dict(
                        color="#F44336", size=10, symbol="triangle-down"
                    ),
                )
            )

        fig.update_layout(
            template="omaha_oracle",
            yaxis_title="Portfolio Value ($)",
            xaxis_title="Date",
            height=500,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab_trades:
        if result["trades"]:
            import pandas as pd

            trades_df = pd.DataFrame(result["trades"])
            trades_df.columns = ["Date", "Ticker", "Signal", "Price", "Shares"]
            st.dataframe(trades_df, use_container_width=True, hide_index=True)
        else:
            st.info("No trades executed in this period.")
