"""Portfolio Overview page."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import DataLoadError, load_portfolio, load_portfolio_history
from dashboard.fmt import (
    fmt_currency,
    fmt_currency_short,
    fmt_delta,
    fmt_pct,
)


def render() -> None:
    """Render the Portfolio Overview page."""
    st.title("Portfolio Overview")
    st.caption(
        "Live positions, allocation, and risk guardrails — powered by Alpaca brokerage data."
    )

    try:
        with st.spinner("Fetching portfolio positions from Alpaca..."):
            data = load_portfolio()
    except DataLoadError as exc:
        st.error(str(exc))
        return

    cash = data.get("cash", 0)
    total = data.get("portfolio_value", 0)
    positions = data.get("positions", [])

    # Compute aggregate gain/loss
    total_cost = sum(p.get("cost_basis", 0) or 0 for p in positions)
    total_mv = sum(p.get("market_value", 0) or 0 for p in positions)
    total_gain = total_mv - total_cost
    total_gain_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0
    cash_pct = (cash / total * 100) if total > 0 else 0

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4, col5 = st.columns(5, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric(
            "Portfolio Value",
            fmt_currency_short(total),
            help="Total market value of all holdings plus uninvested cash.",
        )
    with col2:
        st.metric(
            "Total Gain/Loss",
            fmt_currency_short(total_gain),
            delta=fmt_delta(total_gain_pct),
            help="Unrealized profit or loss across all "
            "positions, measured against original cost basis.",
        )
    with col3:
        st.metric(
            "Positions",
            len(positions),
            help="Number of stocks currently held.",
        )
    with col4:
        st.metric(
            "Cash",
            fmt_currency_short(cash),
            help="Cash available for new positions.",
        )
    with col5:
        st.metric(
            "Cash %",
            fmt_pct(cash_pct),
            help="Cash as percentage of total portfolio. "
            "Must stay above 10% — new buys are blocked "
            "if it drops below.",
        )

    # Warn on risk guardrail breaches
    if total > 0 and cash_pct < 10:
        st.warning(
            f"Cash reserve is {fmt_pct(cash_pct)} — below the "
            "10% minimum. New buys are blocked until cash is "
            "replenished."
        )

    with st.popover("How are risk guardrails enforced?"):
        st.markdown(
            "Hard-coded rules that **cannot** be overridden by "
            "AI analysis:\n\n"
            "- **Max 15%** of portfolio in any single stock\n"
            "- **Max 35%** sector concentration\n"
            "- **Min 10%** cash reserve at all times\n"
            "- **Zero** leverage, shorts, options, or crypto\n\n"
            "Position sizes use the **Half-Kelly criterion** — "
            "a conservative formula that sizes bets based on "
            "estimated edge and win probability, halved to "
            "reduce volatility."
        )

    st.divider()

    if not positions:
        st.info(
            "No positions on record. The portfolio is 100% cash. "
            "Positions will appear after the next analysis cycle "
            "generates a BUY signal."
        )
        return

    # Build position rows once for reuse
    pos_rows = []
    for p in positions:
        cost = p.get("cost_basis", 0) or 0
        mv = p.get("market_value", 0) or 0
        gain = mv - cost
        gain_pct = (gain / cost * 100) if cost > 0 else 0
        pos_rows.append(
            {
                "Ticker": p.get("ticker", ""),
                "Shares": p.get("shares", 0),
                "Cost Basis": cost,
                "Market Value": mv,
                "Gain $": gain,
                "Gain %": gain_pct,
                "Sector": p.get("sector", ""),
            }
        )
    pos_df = pd.DataFrame(pos_rows)
    pos_column_config = {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Shares": st.column_config.NumberColumn("Shares", format="%d"),
        "Cost Basis": st.column_config.NumberColumn("Cost Basis", format="$%,.0f"),
        "Market Value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        "Gain $": st.column_config.NumberColumn("Gain $", format="$%,.0f"),
        "Gain %": st.column_config.NumberColumn("Gain %", format="%+.1f%%"),
        "Sector": st.column_config.TextColumn("Sector"),
    }

    # ── Tier 2: Primary content in tabs ──
    tab_positions, tab_allocation, tab_performance = st.tabs(
        ["Positions", "Allocation", "Performance"]
    )

    with tab_positions:
        with st.container(border=True):
            st.dataframe(
                pos_df,
                column_config=pos_column_config,
                use_container_width=True,
                hide_index=True,
                height=min(len(pos_rows) * 35 + 38, 400),
            )

    with tab_allocation:
        # Sector allocation breakdown
        sector_mv: dict[str, float] = {}
        for p in positions:
            sector = p.get("sector", "Unknown") or "Unknown"
            sector_mv[sector] = sector_mv.get(sector, 0) + (p.get("market_value", 0) or 0)

        if sector_mv:
            alloc_rows = []
            for sector, mv in sorted(
                sector_mv.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                weight = (mv / total * 100) if total > 0 else 0
                alloc_rows.append(
                    {
                        "Sector": sector,
                        "Market Value": fmt_currency(mv),
                        "Weight": fmt_pct(weight),
                    }
                )

            # Warn if any sector exceeds 35% guardrail
            for row in alloc_rows:
                pct = float(row["Weight"].rstrip("%").replace(",", ""))
                if pct > 35:
                    st.warning(
                        f"Sector **{row['Sector']}** is at "
                        f"{row['Weight']} — exceeds the 35% "
                        "sector concentration limit."
                    )

            import plotly.graph_objects as go

            from dashboard.charts import (
                ACCENT_BLUE,
                MUTED_GRAY,
            )

            # Sort ascending for horizontal bar (largest at top)
            sorted_sectors = sorted(sector_mv.items(), key=lambda x: x[1])
            sectors = [s for s, _ in sorted_sectors]
            values = [v for _, v in sorted_sectors]
            weights = [(v / total * 100) if total > 0 else 0 for v in values]

            left, right = st.columns([2, 1], gap="medium")
            with left:
                with st.container(border=True):
                    fig = go.Figure(
                        go.Bar(
                            y=sectors,
                            x=weights,
                            orientation="h",
                            marker_color=ACCENT_BLUE,
                            hovertemplate=("<b>%{y}</b><br>Weight: %{x:.1f}%<extra></extra>"),
                        )
                    )
                    fig.add_vline(
                        x=35,
                        line_dash="dash",
                        line_color=MUTED_GRAY,
                        annotation_text="35% limit",
                        annotation_position="top right",
                        annotation_font_color=MUTED_GRAY,
                    )
                    fig.update_layout(
                        title=("Sector Allocation — % of Portfolio (35% max)"),
                        xaxis_title=None,
                        yaxis_title=None,
                        xaxis_ticksuffix="%",
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption(
                        "Dashed line marks the 35% sector "
                        "concentration limit. Bars crossing "
                        "it trigger a warning and block new "
                        "buys in that sector."
                    )
            with right:
                with st.container(border=True):
                    st.subheader("Breakdown")
                    st.dataframe(
                        alloc_rows,
                        use_container_width=True,
                        hide_index=True,
                        height=min(len(alloc_rows) * 35 + 38, 300),
                    )
        else:
            st.info("No sector data available for allocation chart.")

    with tab_performance:
        try:
            with st.spinner("Loading performance history..."):
                perf = load_portfolio_history()
        except DataLoadError as exc:
            st.error(str(exc))
            perf = {"dates": [], "spy_values": [], "metrics": {}}

        dates = perf.get("dates", [])
        spy_values = perf.get("spy_values", [])
        metrics = perf.get("metrics", {})

        if not dates:
            st.info("No decision history available yet.")
        else:
            # Hero metrics
            pc1, pc2, pc3, pc4 = st.columns(4, gap="large")
            with pc1:
                st.metric(
                    "S&P 500 Return",
                    fmt_pct(metrics.get("spy_return_pct", 0)),
                    help="SPY total return since first decision.",
                )
            with pc2:
                st.metric(
                    "Total Decisions",
                    metrics.get("total_decisions", 0),
                    help="Total buy/sell decisions made.",
                )
            with pc3:
                st.metric(
                    "Buys",
                    metrics.get("total_buys", 0),
                    help="Number of BUY decisions.",
                )
            with pc4:
                st.metric(
                    "Sells",
                    metrics.get("total_sells", 0),
                    help="Number of SELL decisions.",
                )

            # Benchmark chart with decision markers
            with st.container(border=True):
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=spy_values,
                        mode="lines",
                        name="S&P 500 (SPY)",
                        line=dict(color="#6C9EFF"),
                    )
                )

                # Add BUY markers
                buy_dates = perf.get("buy_dates", [])
                if buy_dates:
                    # Map buy dates to SPY values for y-position
                    date_to_spy = dict(zip(dates, spy_values))
                    buy_y = [date_to_spy.get(d) for d in buy_dates]
                    valid = [(d, y) for d, y in zip(buy_dates, buy_y) if y is not None]
                    if valid:
                        fig.add_trace(
                            go.Scatter(
                                x=[d for d, _ in valid],
                                y=[y for _, y in valid],
                                mode="markers",
                                name="BUY",
                                marker=dict(
                                    symbol="triangle-up",
                                    size=12,
                                    color="#00C853",
                                ),
                            )
                        )

                # Add SELL markers
                sell_dates = perf.get("sell_dates", [])
                if sell_dates:
                    date_to_spy = dict(zip(dates, spy_values))
                    sell_y = [date_to_spy.get(d) for d in sell_dates]
                    valid = [(d, y) for d, y in zip(sell_dates, sell_y) if y is not None]
                    if valid:
                        fig.add_trace(
                            go.Scatter(
                                x=[d for d, _ in valid],
                                y=[y for _, y in valid],
                                mode="markers",
                                name="SELL",
                                marker=dict(
                                    symbol="triangle-down",
                                    size=12,
                                    color="#FF1744",
                                ),
                            )
                        )

                fig.update_layout(
                    template="omaha_oracle",
                    yaxis_title="Indexed Value (100 = start)",
                    xaxis_title="Date",
                    height=450,
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(
                    "S&P 500 (SPY) indexed to 100 at the date of the first decision. "
                    "Green/red triangles mark BUY/SELL decisions."
                )

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Raw Position Data"):
        st.json(list(positions))
