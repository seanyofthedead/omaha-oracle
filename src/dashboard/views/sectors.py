"""Sector Rotation Insights — sector performance and portfolio weight analysis."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard.data import DataLoadError, load_portfolio


# Sector ETF mapping for performance data
_SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


def render() -> None:
    st.header("Sector Insights")
    st.caption(
        "Compare portfolio sector weights against market performance"
        " and identify rotation opportunities."
    )

    # Load portfolio
    try:
        portfolio = load_portfolio()
    except DataLoadError as e:
        st.error(str(e))
        return

    positions = portfolio.get("positions", [])
    portfolio_value = portfolio.get("portfolio_value", 0)

    # Calculate portfolio sector weights
    sector_weights: dict[str, float] = {}
    for pos in positions:
        sector = pos.get("sector", "Unknown")
        value = pos.get("market_value", 0)
        sector_weights[sector] = sector_weights.get(sector, 0) + value

    sector_pcts = {
        s: (v / portfolio_value * 100 if portfolio_value > 0 else 0)
        for s, v in sector_weights.items()
    }

    # Hero metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sectors Held", len(sector_weights))
    max_sector = max(sector_pcts, key=sector_pcts.get) if sector_pcts else "N/A"
    max_sector_pct = sector_pcts.get(max_sector, 0) if max_sector != "N/A" else 0
    c2.metric("Largest Sector", max_sector)
    c3.metric("Largest Weight", f"{max_sector_pct:.1f}%")
    c4.metric("35% Limit Headroom", f"{max(0, 35 - max_sector_pct):.1f}%")

    st.divider()

    tab_weights, tab_performance, tab_heatmap = st.tabs(
        ["Portfolio Weights", "Sector Performance", "Heatmap"]
    )

    with tab_weights:
        if not sector_pcts:
            st.info("No positions in portfolio.")
            return

        # Horizontal bar chart of portfolio sector weights
        sorted_sectors = sorted(sector_pcts.items(), key=lambda x: x[1], reverse=True)
        sectors = [s[0] for s in sorted_sectors]
        weights = [s[1] for s in sorted_sectors]

        fig = go.Figure()
        colors = [
            "#F44336" if w > 35 else "#FF9800" if w > 25 else "#6C9EFF"
            for w in weights
        ]
        fig.add_trace(
            go.Bar(
                x=weights,
                y=sectors,
                orientation="h",
                marker_color=colors,
                text=[f"{w:.1f}%" for w in weights],
                textposition="auto",
            )
        )
        # Add 35% limit line
        fig.add_vline(
            x=35,
            line_dash="dash",
            line_color="rgba(244,67,54,0.5)",
            annotation_text="35% Limit",
        )
        fig.update_layout(
            template="omaha_oracle",
            xaxis_title="Portfolio Weight (%)",
            yaxis_title="",
            height=max(300, len(sectors) * 50),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Sector detail table
        import pandas as pd

        sector_data = []
        for sector, pct in sorted_sectors:
            value = sector_weights.get(sector, 0)
            count = sum(1 for p in positions if p.get("sector") == sector)
            sector_data.append(
                {
                    "Sector": sector,
                    "Weight %": round(pct, 1),
                    "Value": value,
                    "Positions": count,
                    "Headroom": round(max(0, 35 - pct), 1),
                }
            )
        df = pd.DataFrame(sector_data)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Value": st.column_config.NumberColumn(format="$%,.0f"),
                "Weight %": st.column_config.ProgressColumn(
                    min_value=0, max_value=50, format="%.1f%%"
                ),
            },
        )

    with tab_performance:
        st.subheader("Sector ETF Performance")
        st.caption(
            "YTD and 1-year returns for sector ETFs."
            " Compares your portfolio allocation to market performance."
        )

        period = st.radio(
            "Period", ["1mo", "3mo", "6mo", "1y"], index=3, horizontal=True
        )

        try:
            import yfinance as yf

            etf_tickers = list(_SECTOR_ETFS.values())
            with st.spinner("Fetching sector performance..."):
                data = yf.download(etf_tickers, period=period, progress=False)

            if not data.empty:
                # Calculate returns
                close = (
                    data["Close"]
                    if "Close" in data.columns.get_level_values(0)
                    else data
                )
                returns = {}
                for sector, etf in _SECTOR_ETFS.items():
                    if etf in close.columns:
                        series = close[etf].dropna()
                        if len(series) >= 2:
                            ret = (series.iloc[-1] / series.iloc[0] - 1) * 100
                            returns[sector] = round(float(ret), 2)

                if returns:
                    sorted_returns = sorted(
                        returns.items(), key=lambda x: x[1], reverse=True
                    )
                    sectors_r = [s[0] for s in sorted_returns]
                    rets = [s[1] for s in sorted_returns]
                    colors_r = [
                        "#4CAF50" if r >= 0 else "#F44336" for r in rets
                    ]

                    # Mark sectors we own
                    annotations = []
                    for s in sectors_r:
                        if s in sector_pcts:
                            annotations.append(
                                f"{s} ({sector_pcts[s]:.0f}% held)"
                            )
                        else:
                            annotations.append(s)

                    fig2 = go.Figure()
                    fig2.add_trace(
                        go.Bar(
                            x=rets,
                            y=annotations,
                            orientation="h",
                            marker_color=colors_r,
                            text=[f"{r:+.1f}%" for r in rets],
                            textposition="auto",
                        )
                    )
                    fig2.update_layout(
                        template="omaha_oracle",
                        xaxis_title=f"Return ({period})",
                        height=max(350, len(sectors_r) * 45),
                    )
                    st.plotly_chart(fig2, use_container_width=True)

                    # Opportunity callout
                    held_sectors = set(sector_pcts.keys())
                    top_performers = [
                        s
                        for s, r in sorted_returns[:3]
                        if s not in held_sectors
                    ]
                    if top_performers:
                        st.info(
                            "Top-performing sectors not in your portfolio:"
                            f" **{', '.join(top_performers)}**"
                        )
                else:
                    st.warning(
                        "Could not calculate returns from price data."
                    )
            else:
                st.warning("No sector ETF data available.")
        except ImportError:
            st.info("yfinance not installed — sector performance unavailable.")
        except Exception as e:
            st.warning(f"Could not fetch sector data: {e}")

    with tab_heatmap:
        st.subheader("Portfolio Concentration Heatmap")
        st.caption(
            "Visualize how your portfolio is distributed across sectors"
            " and individual positions."
        )

        if not positions:
            st.info("No positions to display.")
            return

        # Build treemap data
        labels = []
        parents = []
        values = []
        colors_tm = []

        for sector in sorted(sector_weights.keys()):
            labels.append(sector)
            parents.append("Portfolio")
            values.append(sector_weights[sector])
            pct = sector_pcts.get(sector, 0)
            colors_tm.append(
                "#F44336" if pct > 35 else "#FF9800" if pct > 25 else "#6C9EFF"
            )

            for pos in positions:
                if pos.get("sector") == sector:
                    ticker = pos.get("ticker", "?")
                    mv = pos.get("market_value", 0)
                    labels.append(ticker)
                    parents.append(sector)
                    values.append(mv)
                    pos_pct = (
                        (mv / portfolio_value * 100) if portfolio_value > 0 else 0
                    )
                    colors_tm.append(
                        "#F44336" if pos_pct > 15 else "#4CAF50"
                    )

        labels.insert(0, "Portfolio")
        parents.insert(0, "")
        values.insert(0, portfolio_value)
        colors_tm.insert(0, "#333333")

        fig3 = go.Figure(
            go.Treemap(
                labels=labels,
                parents=parents,
                values=values,
                marker=dict(colors=colors_tm),
                textinfo="label+percent parent",
            )
        )
        fig3.update_layout(
            template="omaha_oracle",
            height=500,
            margin=dict(t=30, b=10, l=10, r=10),
        )
        st.plotly_chart(fig3, use_container_width=True)
