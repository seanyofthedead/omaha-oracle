"""Performance Analytics tab — returns, benchmark comparison, and risk metrics.

Combines Alpaca portfolio history with SPY benchmark data and trade
statistics to give a comprehensive performance overview.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.alpaca_session import get_alpaca_client
from dashboard.analytics import (
    PortfolioHistory,
    build_journal_entries,
    compute_all_metrics,
    prepare_equity_chart_data,
)
from dashboard.charts import ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED, get_chart_template
from dashboard.fmt import fmt_currency, fmt_pct

# ── Data fetching ────────────────────────────────────────────────────────


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_portfolio_history(period: str, timeframe: str) -> dict | None:
    client = get_alpaca_client()
    h = client.get_portfolio_history(period=period, timeframe=timeframe)
    return {
        "timestamps": h.timestamps,
        "equity": h.equity,
        "profit_loss_pct": h.profit_loss_pct,
        "base_value": h.base_value,
    }


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_closed_orders() -> list[dict]:
    client = get_alpaca_client()
    orders = client.get_orders(status="closed", limit=500)
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "side": o.side,
            "qty": o.qty,
            "order_type": o.order_type,
            "time_in_force": o.time_in_force,
            "status": o.status,
            "created_at": o.created_at,
            "filled_at": o.filled_at,
            "filled_avg_price": o.filled_avg_price,
            "limit_price": o.limit_price,
            "stop_price": o.stop_price,
        }
        for o in orders
    ]


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_spy_data(start_date: str) -> dict | None:
    """Fetch SPY price data for benchmark comparison."""
    import yfinance as yf

    spy = yf.download("SPY", start=start_date, progress=False)
    if spy.empty:
        return None
    closes = spy["Close"].dropna()
    dates = [d.strftime("%Y-%m-%d") for d in closes.index]
    values = closes.tolist()
    return {"dates": dates, "values": values}


# ── Render ───────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Performance Analytics tab."""
    st.header("Performance Analytics")

    # ── Period selector ──────────────────────────────────────────────
    period_map = {
        "1 Month": ("1M", "1D"),
        "3 Months": ("3M", "1D"),
        "1 Year": ("1A", "1D"),
        "All Time": ("all", "1D"),
    }

    selected = st.radio(
        "Period",
        list(period_map.keys()),
        horizontal=True,
        key="perf_period",
        label_visibility="collapsed",
    )

    period, timeframe = period_map[selected]

    # ── Fetch data ───────────────────────────────────────────────────
    try:
        raw_history = _fetch_portfolio_history(period, timeframe)
    except Exception:
        raw_history = None

    if raw_history is None or not raw_history["timestamps"]:
        st.info("No portfolio history available. Start trading to see performance metrics.")
        return

    history = PortfolioHistory(
        timestamps=raw_history["timestamps"],
        equity=raw_history["equity"],
        profit_loss_pct=raw_history["profit_loss_pct"],
        base_value=raw_history["base_value"],
    )

    df = prepare_equity_chart_data(history)

    # ── Trade metrics ────────────────────────────────────────────────
    try:
        order_dicts = _fetch_closed_orders()
    except Exception as exc:
        handle_alpaca_error(exc)
        order_dicts = []

    from dashboard.alpaca_models import OrderInfo

    order_objects = [OrderInfo(**{k: v for k, v in o.items()}) for o in order_dicts]
    journal = build_journal_entries(order_objects)
    pnl_list = [e["pnl"] for e in journal]

    # ── Compute returns ──────────────────────────────────────────────
    equity = history.equity
    if len(equity) >= 2 and equity[0] > 0:
        portfolio_return = (equity[-1] / equity[0] - 1) * 100
    else:
        portfolio_return = 0.0

    metrics = compute_all_metrics(pnl_list, equity)

    # ── Hero metrics ─────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Total Return", fmt_pct(portfolio_return))
    with c2:
        st.metric("Max Drawdown", fmt_pct(metrics["max_drawdown"]))
    with c3:
        st.metric("Win Rate", fmt_pct(metrics["win_rate"]))
    with c4:
        pf = metrics["profit_factor"]
        st.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "\u221e")
    with c5:
        st.metric("Sharpe Ratio", f"{metrics['sharpe_ratio']:.2f}")

    st.divider()

    # ── Performance chart ────────────────────────────────────────────
    st.subheader("Portfolio Equity vs SPY")

    fig = go.Figure()

    # Portfolio equity (rebased to 100)
    if equity and equity[0] > 0:
        rebased = [e / equity[0] * 100 for e in equity]
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=rebased,
                mode="lines",
                name="Portfolio",
                line={"color": ACCENT_BLUE, "width": 2},
            )
        )

    # SPY benchmark
    if df is not None and len(df) > 0:
        start_str = df["date"].iloc[0].strftime("%Y-%m-%d")
        try:
            spy_data = _fetch_spy_data(start_str)
        except Exception:
            spy_data = None
        if spy_data and spy_data["values"]:
            spy_vals = spy_data["values"]
            first = spy_vals[0] if isinstance(spy_vals[0], (int, float)) else float(spy_vals[0])
            if first > 0:
                spy_rebased = [float(v) / first * 100 for v in spy_vals]
                fig.add_trace(
                    go.Scatter(
                        x=spy_data["dates"],
                        y=spy_rebased,
                        mode="lines",
                        name="SPY",
                        line={"color": ACCENT_GREEN, "width": 1, "dash": "dash"},
                    )
                )

    fig.update_layout(
        template=get_chart_template(),
        yaxis_title="Rebased (100 = start)",
        xaxis_title="",
        height=400,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    st.plotly_chart(fig, width="stretch")

    # ── Drawdown chart ───────────────────────────────────────────────
    st.subheader("Drawdown")

    if len(equity) >= 2:
        peak = equity[0]
        drawdowns = []
        for val in equity:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            drawdowns.append(-dd)

        fig_dd = go.Figure()
        fig_dd.add_trace(
            go.Scatter(
                x=df["date"],
                y=drawdowns,
                mode="lines",
                fill="tozeroy",
                line={"color": ACCENT_RED, "width": 1},
                fillcolor="rgba(244,67,54,0.15)",
                hovertemplate="<b>%{x|%b %d, %Y}</b><br>Drawdown: %{y:.1f}%<extra></extra>",
            )
        )
        fig_dd.update_layout(
            template=get_chart_template(),
            yaxis_title="Drawdown (%)",
            xaxis_title="",
            height=250,
        )
        st.plotly_chart(fig_dd, width="stretch")

    # ── Trade statistics ─────────────────────────────────────────────
    if journal:
        st.subheader("Trade Statistics")

        ts1, ts2, ts3, ts4 = st.columns(4)
        with ts1:
            st.metric("Total Trades", len(journal))
        with ts2:
            st.metric("Avg Win", fmt_currency(metrics["avg_win"], decimals=2))
        with ts3:
            st.metric("Avg Loss", fmt_currency(metrics["avg_loss"], decimals=2))
        with ts4:
            largest_win = max(pnl_list) if pnl_list else 0
            largest_loss = min(pnl_list) if pnl_list else 0
            st.metric("Largest Win", fmt_currency(largest_win, decimals=2))
            st.metric("Largest Loss", fmt_currency(largest_loss, decimals=2))
