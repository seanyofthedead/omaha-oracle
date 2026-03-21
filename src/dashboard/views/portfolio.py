"""Portfolio Overview page."""

from __future__ import annotations

import streamlit as st

from dashboard.data import DataLoadError, load_portfolio
from dashboard.fmt import (
    fmt_currency,
    fmt_currency_short,
    fmt_delta,
    fmt_null,
    fmt_pct,
)


def render() -> None:
    """Render the Portfolio Overview page."""
    st.title("Portfolio Overview")
    st.caption(
        "Live positions, allocation, and risk guardrails — "
        "powered by Alpaca brokerage data."
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
    total_gain_pct = (
        (total_gain / total_cost * 100) if total_cost > 0 else 0
    )
    cash_pct = (cash / total * 100) if total > 0 else 0

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4, col5 = st.columns(
        5, gap="large", vertical_alignment="bottom"
    )
    with col1:
        st.metric(
            "Portfolio Value",
            fmt_currency_short(total),
            help="Total market value of all holdings plus "
            "uninvested cash.",
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
                "Cost Basis": fmt_currency(cost),
                "Market Value": fmt_currency(mv),
                "Gain/Loss": (
                    f"{fmt_currency(gain)} ({fmt_delta(gain_pct)})"
                ),
                "Sector": p.get("sector", ""),
                "Thesis": fmt_null(p.get("thesis_link")),
            }
        )

    # ── Tier 2: Primary content in tabs ──
    tab_positions, tab_allocation = st.tabs(
        ["Positions", "Allocation"]
    )

    with tab_positions:
        with st.container(border=True):
            st.dataframe(
                pos_rows,
                use_container_width=True,
                hide_index=True,
            )

    with tab_allocation:
        # Sector allocation breakdown
        sector_mv: dict[str, float] = {}
        for p in positions:
            sector = p.get("sector", "Unknown") or "Unknown"
            sector_mv[sector] = sector_mv.get(sector, 0) + (
                p.get("market_value", 0) or 0
            )

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
            sorted_sectors = sorted(
                sector_mv.items(), key=lambda x: x[1]
            )
            sectors = [s for s, _ in sorted_sectors]
            values = [v for _, v in sorted_sectors]
            weights = [
                (v / total * 100) if total > 0 else 0
                for v in values
            ]

            left, right = st.columns([2, 1], gap="medium")
            with left:
                with st.container(border=True):
                    fig = go.Figure(
                        go.Bar(
                            y=sectors,
                            x=weights,
                            orientation="h",
                            marker_color=ACCENT_BLUE,
                            hovertemplate=(
                                "<b>%{y}</b><br>"
                                "Weight: %{x:.1f}%"
                                "<extra></extra>"
                            ),
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
                        title=(
                            "Sector Allocation"
                            " — % of Portfolio (35% max)"
                        ),
                        xaxis_title=None,
                        yaxis_title=None,
                        xaxis_ticksuffix="%",
                        showlegend=False,
                    )
                    st.plotly_chart(
                        fig, use_container_width=True
                    )
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
                    )
        else:
            st.info(
                "No sector data available for allocation chart."
            )

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Raw Position Data"):
        st.json(list(positions))
