"""Watchlist page — companies under analysis."""

from __future__ import annotations

import streamlit as st

from dashboard.data import DataLoadError, load_watchlist_analysis
from dashboard.fmt import fmt_currency, fmt_null, fmt_pct


def render() -> None:
    """Render the Watchlist page."""
    st.title("Watchlist")
    st.caption(
        "Stocks under active analysis — screened quantitatively, "
        "then evaluated by AI for moat strength, management "
        "quality, and intrinsic value."
    )

    try:
        with st.spinner(
            "Querying watchlist analysis across all candidates..."
        ):
            candidates = load_watchlist_analysis()
    except DataLoadError as exc:
        st.error(str(exc))
        return

    # Hero metric row
    n = len(candidates)
    moat_scores = [
        c.get("moat_score") or 0
        for c in candidates
        if c.get("moat_score")
    ]
    mgmt_scores = [
        c.get("management_score") or 0
        for c in candidates
        if c.get("management_score")
    ]
    mos_values = []
    for c in candidates:
        iv = (
            c.get("intrinsic_value_per_share")
            or c.get("intrinsic_value")
            or 0
        )
        price = c.get("current_price") or 0
        if iv > 0:
            mos_values.append((iv - price) / iv * 100)

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4 = st.columns(
        4, gap="large", vertical_alignment="bottom"
    )
    with col1:
        st.metric(
            "Candidates",
            n,
            help="Stocks that passed the quantitative screen "
            "and are being evaluated by the AI pipeline.",
        )
    with col2:
        avg_mos = (
            sum(mos_values) / len(mos_values) if mos_values else 0
        )
        st.metric(
            "Avg Margin of Safety",
            fmt_pct(avg_mos, 0) if mos_values else fmt_null(None),
            help="How far below intrinsic value the average "
            "candidate trades. Higher is safer — Buffett "
            "typically wants 25%+.",
        )
    with col3:
        avg_moat = (
            sum(moat_scores) / len(moat_scores)
            if moat_scores
            else 0
        )
        st.metric(
            "Avg Moat Score",
            f"{avg_moat:.1f}" if moat_scores else fmt_null(None),
            help="AI-assessed competitive advantage strength "
            "(1–10). Measures pricing power, switching costs, "
            "and network effects.",
        )
    with col4:
        avg_mgmt = (
            sum(mgmt_scores) / len(mgmt_scores)
            if mgmt_scores
            else 0
        )
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

    if not candidates:
        st.info(
            "No watchlist candidates have analysis yet. "
            "Tickers are added to the watchlist during ingestion "
            "and analyzed in the next pipeline run."
        )
        return

    # Flag candidates missing key scores
    missing_iv = [
        c.get("ticker", "?")
        for c in candidates
        if not (
            c.get("intrinsic_value_per_share")
            or c.get("intrinsic_value")
        )
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
    for c in candidates:
        ticker = c.get("ticker", "")
        sector = c.get("sector", "")
        moat = c.get("moat_score") or 0
        mgmt = c.get("management_score") or 0
        iv = (
            c.get("intrinsic_value_per_share")
            or c.get("intrinsic_value")
            or 0
        )
        price = c.get("current_price") or 0
        mos = ((iv - price) / iv * 100) if iv > 0 else 0

        valuation_rows.append(
            {
                "Ticker": ticker,
                "IV": fmt_currency(iv) if iv else fmt_null(None),
                "Price": (
                    fmt_currency(price)
                    if price
                    else fmt_null(None)
                ),
                "MoS %": (
                    fmt_pct(mos, 0) if iv else fmt_null(None)
                ),
                "Sector": sector,
            }
        )
        quality_rows.append(
            {
                "Ticker": ticker,
                "Moat": f"{moat:.1f}" if moat else fmt_null(None),
                "Mgmt": f"{mgmt:.1f}" if mgmt else fmt_null(None),
                "Combined": (
                    f"{moat + mgmt:.1f}"
                    if moat and mgmt
                    else fmt_null(None)
                ),
                "Sector": sector,
            }
        )

    # ── Tier 2: Primary content in tabs ──
    tab_valuation, tab_quality = st.tabs(
        ["Valuation", "Quality Scores"]
    )

    with tab_valuation:
        with st.container(border=True):
            st.dataframe(
                valuation_rows,
                use_container_width=True,
                hide_index=True,
            )

    with tab_quality:
        with st.container(border=True):
            st.dataframe(
                quality_rows,
                use_container_width=True,
                hide_index=True,
            )

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Raw Analysis Data"):
        st.json(candidates)
