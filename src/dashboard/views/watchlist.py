"""Watchlist page — companies under analysis."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import DataLoadError, load_watchlist_analysis
from dashboard.fmt import fmt_null, fmt_pct, render_export_button

# Colors for radar chart traces
_COMPARE_COLORS = ["#6C9EFF", "#4CAF50", "#F44336", "#FF9800"]


def render() -> None:
    """Render the Watchlist page."""
    st.title("Watchlist")
    st.caption(
        "Stocks under active analysis — screened quantitatively, "
        "then evaluated by AI for moat strength, management "
        "quality, and intrinsic value."
    )

    try:
        with st.spinner("Querying watchlist analysis across all candidates..."):
            candidates = load_watchlist_analysis()
    except DataLoadError as exc:
        st.error(str(exc))
        return

    # ── Sidebar filters ──
    unique_sectors = sorted({c.get("sector", "") for c in candidates if c.get("sector")})

    selected_sectors = st.sidebar.multiselect(
        "Sectors", unique_sectors, default=unique_sectors
    )
    min_mos = st.sidebar.slider("Min Margin of Safety %", 0, 100, 0)
    min_moat = st.sidebar.slider("Min Moat Score", 0, 10, 0)

    # Apply filters
    filtered: list[dict] = []
    for c in candidates:
        sector = c.get("sector", "")
        if sector and sector not in selected_sectors:
            continue
        moat = c.get("moat_score") or 0
        if moat < min_moat:
            continue
        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        mos = ((iv - price) / iv * 100) if iv > 0 else 0
        if mos < min_mos:
            continue
        filtered.append(c)

    st.caption(f"Showing {len(filtered)} of {len(candidates)} candidates")

    # Hero metric row
    n = len(filtered)
    moat_scores = [c.get("moat_score") or 0 for c in filtered if c.get("moat_score")]
    mgmt_scores = [c.get("management_score") or 0 for c in filtered if c.get("management_score")]
    mos_values = []
    for c in filtered:
        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        if iv > 0:
            mos_values.append((iv - price) / iv * 100)

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4 = st.columns(4, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric(
            "Candidates",
            n,
            help="Stocks that passed the quantitative screen "
            "and are being evaluated by the AI pipeline.",
        )
    with col2:
        avg_mos = sum(mos_values) / len(mos_values) if mos_values else 0
        st.metric(
            "Avg Margin of Safety",
            fmt_pct(avg_mos, 0) if mos_values else fmt_null(None),
            help="How far below intrinsic value the average "
            "candidate trades. Higher is safer — Buffett "
            "typically wants 25%+.",
        )
    with col3:
        avg_moat = sum(moat_scores) / len(moat_scores) if moat_scores else 0
        st.metric(
            "Avg Moat Score",
            f"{avg_moat:.1f}" if moat_scores else fmt_null(None),
            help="AI-assessed competitive advantage strength "
            "(1–10). Measures pricing power, switching costs, "
            "and network effects.",
        )
    with col4:
        avg_mgmt = sum(mgmt_scores) / len(mgmt_scores) if mgmt_scores else 0
        st.metric(
            "Avg Mgmt Score",
            f"{avg_mgmt:.1f}" if mgmt_scores else fmt_null(None),
            help="AI-assessed management quality (1–10). "
            "Evaluates capital allocation track record, "
            "insider ownership, and shareholder alignment.",
        )

    with st.popover("How is intrinsic value calculated?"):
        st.markdown(
            "Three independent models estimate what each "
            "stock is truly worth:\n\n"
            "1. **DCF** — Discounted cash flow projection\n"
            "2. **EPV** — Earnings power value "
            "(no-growth baseline)\n"
            "3. **Asset floor** — Liquidation value safety "
            "net\n\n"
            "**Margin of Safety** = how far below intrinsic "
            "value the stock currently trades. "
            "MoS = (IV − Price) / IV."
        )

    st.divider()

    if not filtered:
        st.info(
            "No watchlist candidates match the current filters. "
            "Try broadening the sidebar filters, or wait for "
            "the next pipeline run to add new candidates."
        )
        return

    # Flag candidates missing key scores
    missing_iv = [
        c.get("ticker", "?")
        for c in filtered
        if not (c.get("intrinsic_value_per_share") or c.get("intrinsic_value"))
    ]
    if missing_iv:
        st.warning(
            f"{len(missing_iv)} candidate(s) missing intrinsic "
            f"value estimates: {', '.join(missing_iv[:5])}"
            + (" ..." if len(missing_iv) > 5 else "")
            + ". These may still be in the analysis pipeline."
        )

    # Build rows for both tabs
    valuation_rows = []
    quality_rows = []
    for c in filtered:
        ticker = c.get("ticker", "")
        sector = c.get("sector", "")
        moat = c.get("moat_score") or 0
        mgmt = c.get("management_score") or 0
        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        mos = ((iv - price) / iv * 100) if iv > 0 else 0

        valuation_rows.append(
            {
                "Ticker": ticker,
                "IV": iv or None,
                "Price": price or None,
                "MoS %": mos if iv else None,
                "Sector": sector,
            }
        )
        quality_rows.append(
            {
                "Ticker": ticker,
                "Moat": moat or None,
                "Mgmt": mgmt or None,
                "Combined": ((moat + mgmt) if moat and mgmt else None),
                "Sector": sector,
            }
        )

    val_df = pd.DataFrame(valuation_rows)
    qual_df = pd.DataFrame(quality_rows)
    tbl_height = min(len(filtered) * 35 + 38, 400)

    val_column_config = {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "IV": st.column_config.NumberColumn(
            "Intrinsic Value",
            format="$%,.0f",
            help="Blended estimate from DCF, EPV, and asset floor models.",
        ),
        "Price": st.column_config.NumberColumn("Price", format="$%,.0f"),
        "MoS %": st.column_config.NumberColumn(
            "MoS %",
            format="%.0f%%",
            help="Margin of Safety: (IV - Price) / IV. Higher means cheaper relative to value.",
        ),
        "Sector": st.column_config.TextColumn("Sector"),
    }

    qual_column_config = {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Moat": st.column_config.ProgressColumn(
            "Moat",
            min_value=0,
            max_value=10,
            format="%.1f",
            help="AI-assessed competitive advantage (0-10).",
        ),
        "Mgmt": st.column_config.ProgressColumn(
            "Mgmt",
            min_value=0,
            max_value=10,
            format="%.1f",
            help="AI-assessed management quality (0-10).",
        ),
        "Combined": st.column_config.NumberColumn("Combined", format="%.1f"),
        "Sector": st.column_config.TextColumn("Sector"),
    }

    # ── Tier 2: Primary content in tabs ──
    tab_valuation, tab_quality, tab_compare = st.tabs(
        ["Valuation", "Quality Scores", "Compare"]
    )

    with tab_valuation:
        with st.container(border=True):
            st.dataframe(
                val_df,
                column_config=val_column_config,
                use_container_width=True,
                hide_index=True,
                height=tbl_height,
            )
            render_export_button(val_df, "watchlist_valuation", label="Download Watchlist CSV")

    with tab_quality:
        with st.container(border=True):
            st.dataframe(
                qual_df,
                column_config=qual_column_config,
                use_container_width=True,
                hide_index=True,
                height=tbl_height,
            )
            render_export_button(qual_df, "watchlist_quality", label="Download Quality Scores CSV")

    with tab_compare:
        _render_compare_tab(filtered)

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Raw Analysis Data"):
        st.json(candidates)


def _render_compare_tab(filtered: list[dict]) -> None:
    """Render the Compare tab with radar chart and side-by-side metrics."""
    ticker_list = [c.get("ticker", "") for c in filtered if c.get("ticker")]

    selected = st.multiselect(
        "Select tickers to compare (up to 4)", ticker_list, max_selections=4
    )

    if not selected:
        st.info("Select up to 4 tickers above to compare them side by side.")
        return

    # Build lookup for selected candidates
    selected_data: list[dict] = []
    for c in filtered:
        if c.get("ticker") in selected:
            selected_data.append(c)

    # ── Radar chart ──
    categories = ["Moat Score", "Management Score", "Margin of Safety", "Piotroski F-Score"]

    fig = go.Figure()
    for i, c in enumerate(selected_data):
        ticker = c.get("ticker", "")
        moat = c.get("moat_score") or 0
        mgmt = c.get("management_score") or 0

        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        mos_raw = ((iv - price) / iv * 100) if iv > 0 else 0
        mos_scaled = min(mos_raw / 10, 10)  # scale 0-100 to 0-10

        f_score = c.get("piotroski_f_score") or c.get("f_score") or 0
        f_scaled = f_score * 10 / 9  # scale 0-9 to 0-10

        values = [moat, mgmt, mos_scaled, f_scaled]
        # Close the polygon by repeating the first value
        values_closed = values + [values[0]]

        fig.add_trace(
            go.Scatterpolar(
                r=values_closed,
                theta=categories + [categories[0]],
                fill="toself",
                name=ticker,
                line={"color": _COMPARE_COLORS[i % len(_COMPARE_COLORS)]},
                fillcolor=None,
                opacity=0.8,
            )
        )

    fig.update_layout(
        polar={
            "radialaxis": {"visible": True, "range": [0, 10]},
            "bgcolor": "rgba(0,0,0,0)",
        },
        title="Ticker Comparison",
        height=450,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Side-by-side metric cards ──
    st.subheader("Key Stats")
    cols = st.columns(len(selected_data))
    for col, c in zip(cols, selected_data):
        ticker = c.get("ticker", "")
        moat = c.get("moat_score") or 0
        mgmt = c.get("management_score") or 0
        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        mos = ((iv - price) / iv * 100) if iv > 0 else 0
        f_score = c.get("piotroski_f_score") or c.get("f_score") or 0
        sector = c.get("sector", "N/A")

        with col:
            st.markdown(f"**{ticker}**")
            st.caption(sector)
            st.metric("Moat Score", f"{moat:.1f}" if moat else "N/A")
            st.metric("Mgmt Score", f"{mgmt:.1f}" if mgmt else "N/A")
            st.metric("Margin of Safety", fmt_pct(mos, 0) if iv else "N/A")
            st.metric("Piotroski F-Score", f"{f_score}" if f_score else "N/A")
            st.metric("Intrinsic Value", f"${iv:,.0f}" if iv else "N/A")
            st.metric("Current Price", f"${price:,.0f}" if price else "N/A")
