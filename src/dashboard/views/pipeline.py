"""Watchlist & Pipeline tab — bridge between Oracle analysis and paper trading.

Shows Oracle pipeline candidates with analysis scores, cross-referenced
with current Alpaca positions and orders.  Also provides Alpaca watchlist
management and recent Oracle signals.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.alpaca_session import get_alpaca_client
from dashboard.charts import ACCENT_BLUE, ACCENT_GREEN, get_chart_template
from dashboard.data import DataLoadError
from dashboard.fmt import fmt_currency, fmt_date, fmt_pct
from shared.analysis_client import _STAGE_DISPLAY, PIPELINE_STAGE_ORDER

# ── Quant screen thresholds (defaults matching screener.py) ──────────────
_QUANT_THRESHOLDS = {
    "max_pe": 15,
    "max_pb": 1.5,
    "max_debt_equity": 0.5,
    "min_roic_avg": 0.12,
    "min_positive_fcf_years": 8,
    "min_piotroski": 6,
}

_STATUS_LABELS = {
    "thesis_generated": "Thesis Generated",
    "passed_intrinsic_value": "Passed IV",
    "passed_management_quality": "Passed Mgmt",
    "passed_moat_analysis": "Passed Moat",
    "passed_quant_screen": "Passed Quant",
    "failed_thesis_generator": "Failed Thesis",
    "failed_intrinsic_value": "Failed IV",
    "failed_management_quality": "Failed Mgmt",
    "failed_moat_analysis": "Failed Moat",
    "failed_quant_screen": "Failed Quant",
    "no_data": "No Data",
}


# ── Data fetching ────────────────────────────────────────────────────────


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_all_candidates(analysis_date: str | None = None) -> list[dict]:
    """Load all pipeline candidates (pass and fail) from DynamoDB."""
    from dashboard.data import load_all_pipeline_candidates

    return load_all_pipeline_candidates(analysis_date=analysis_date)


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_oracle_candidates() -> list[dict]:
    """Load Oracle pipeline candidates from DynamoDB (watchlist only)."""
    from dashboard.data import load_watchlist_analysis

    return load_watchlist_analysis()


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_run_dates() -> list[str]:
    """Load available pipeline run dates."""
    from dashboard.data import load_pipeline_run_dates

    return load_pipeline_run_dates()


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_recent_decisions(limit: int = 20) -> list[dict]:
    """Load recent Oracle buy/sell signals from DynamoDB."""
    from dashboard.data import load_decisions

    return load_decisions(limit=limit)


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_held_symbols() -> set[str]:
    """Return set of currently held symbols from Alpaca."""
    client = get_alpaca_client()
    positions = client.get_positions()
    return {p.symbol for p in positions}


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_pending_symbols() -> set[str]:
    """Return set of symbols with open orders."""
    client = get_alpaca_client()
    orders = client.get_orders(status="open", limit=100)
    return {o.symbol for o in orders}


# ── Helpers ──────────────────────────────────────────────────────────────


def _safe_float(val: object) -> float:
    """Convert a value (possibly Decimal) to float, defaulting to 0."""
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _extract_failure_reasons(candidate: dict) -> list[str]:
    """Extract human-readable failure reasons from a candidate's stage data."""
    reasons: list[str] = []
    stages = candidate.get("stages", {})

    # Quant screen failures
    quant = stages.get("quant_screen", {})
    if quant.get("passed") is False:
        r = quant.get("result", {})
        pe = _safe_float(r.get("pe"))
        pb = _safe_float(r.get("pb"))
        de = _safe_float(r.get("debt_equity"))
        roic = _safe_float(r.get("roic_10y_avg"))
        fcf = int(_safe_float(r.get("positive_fcf_years")))
        pio = int(_safe_float(r.get("piotroski_score")))

        if pe > 0 and pe >= _QUANT_THRESHOLDS["max_pe"]:
            reasons.append(f"P/E: {pe:.1f} >= {_QUANT_THRESHOLDS['max_pe']}")
        if pb > 0 and pb >= _QUANT_THRESHOLDS["max_pb"]:
            reasons.append(f"P/B: {pb:.1f} >= {_QUANT_THRESHOLDS['max_pb']}")
        if de >= _QUANT_THRESHOLDS["max_debt_equity"]:
            reasons.append(f"D/E: {de:.2f} >= {_QUANT_THRESHOLDS['max_debt_equity']}")
        if roic < _QUANT_THRESHOLDS["min_roic_avg"]:
            min_roic = _QUANT_THRESHOLDS["min_roic_avg"] * 100
            reasons.append(f"ROIC: {roic * 100:.1f}% < {min_roic:.0f}%")
        if fcf < _QUANT_THRESHOLDS["min_positive_fcf_years"]:
            reasons.append(f"FCF yrs: {fcf} < {_QUANT_THRESHOLDS['min_positive_fcf_years']}")
        if pio < _QUANT_THRESHOLDS["min_piotroski"]:
            reasons.append(f"Piotroski: {pio} < {_QUANT_THRESHOLDS['min_piotroski']}")

    # Moat failure
    moat = stages.get("moat_analysis", {})
    if moat.get("passed") is False:
        score = _safe_float(moat.get("result", {}).get("moat_score"))
        reasons.append(f"Moat: {score:.0f} < 7")

    # Management failure
    mgmt = stages.get("management_quality", {})
    if mgmt.get("passed") is False:
        score = _safe_float(mgmt.get("result", {}).get("management_score"))
        reasons.append(f"Mgmt: {score:.0f} < 6")

    # IV failure
    iv = stages.get("intrinsic_value", {})
    if iv.get("passed") is False:
        mos = _safe_float(iv.get("result", {}).get("margin_of_safety"))
        reasons.append(f"MoS: {mos * 100:.1f}% <= 30%")

    return reasons


# ── Render ───────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Watchlist & Pipeline tab."""
    st.header("Watchlist & Pipeline")

    try:
        held = _fetch_held_symbols()
    except Exception:
        st.error("Failed to load held positions from Alpaca.")
        held = set()

    try:
        pending = _fetch_pending_symbols()
    except Exception:
        st.error("Failed to load pending orders from Alpaca.")
        pending = set()

    tabs = st.tabs(
        [
            "Oracle Pipeline",
            "All Scanned Candidates",
            "Run Pipeline",
            "Alpaca Watchlists",
            "Recent Signals",
        ]
    )
    tab_pipeline, tab_all_candidates, tab_run, tab_watchlists, tab_signals = tabs

    with tab_pipeline:
        try:
            candidates = _fetch_oracle_candidates()
        except (DataLoadError, Exception) as exc:
            st.error(f"Failed to load pipeline candidates: {exc}")
            candidates = []
        _render_pipeline(candidates, held, pending)

    with tab_all_candidates:
        _render_all_candidates(held, pending)

    with tab_run:
        _render_run_pipeline()

    with tab_watchlists:
        _render_watchlists()

    with tab_signals:
        _render_signals()


def _render_all_candidates(held: set[str], pending: set[str]) -> None:
    """Render all scanned candidates with funnel and detailed table."""
    # Date selector
    try:
        run_dates = _fetch_run_dates()
    except (DataLoadError, Exception) as exc:
        st.error(f"Failed to load run dates: {exc}")
        run_dates = []

    selected_date: str | None = None
    if run_dates:
        date_options = ["Latest"] + run_dates
        choice = st.selectbox(
            "Analysis Run Date",
            date_options,
            key="pipeline_run_date",
        )
        if choice != "Latest":
            selected_date = choice

    try:
        candidates = _fetch_all_candidates(analysis_date=selected_date)
    except (DataLoadError, Exception) as exc:
        st.error(f"Failed to load candidates: {exc}")
        candidates = []

    if not candidates:
        st.info(
            "No scanned candidates found. The Oracle analysis pipeline "
            "populates this view when the quant screen runs."
        )
        return

    # ── Hero metrics ─────────────────────────────────────────────────
    total = len(candidates)
    passed_all = [c for c in candidates if c.get("pipeline_status") == "thesis_generated"]
    mos_values = [
        _safe_float(c.get("margin_of_safety_pct", 0))
        for c in passed_all
        if c.get("margin_of_safety_pct")
    ]
    avg_mos = sum(mos_values) / len(mos_values) if mos_values else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Screened", total)
    with c2:
        st.metric("Passed All Stages", len(passed_all))
    with c3:
        pass_rate = len(passed_all) / total * 100 if total else 0
        st.metric("Pass Rate", f"{pass_rate:.1f}%")
    with c4:
        st.metric("Avg MoS (Passed)", fmt_pct(avg_mos))

    st.divider()

    # ── Funnel chart ─────────────────────────────────────────────────
    _render_funnel(candidates)

    st.divider()

    # ── Filters ──────────────────────────────────────────────────────
    col_status, col_sector, col_sort = st.columns(3)

    all_statuses = sorted(
        {_STATUS_LABELS.get(c.get("pipeline_status", ""), "Unknown") for c in candidates}
    )
    with col_status:
        status_filter = st.multiselect(
            "Filter by Status",
            all_statuses,
            key="pipeline_status_filter",
        )

    all_sectors = sorted({c.get("sector", "Unknown") for c in candidates})
    with col_sector:
        sector_filter = st.multiselect(
            "Filter by Sector",
            all_sectors,
            key="pipeline_sector_filter",
        )

    with col_sort:
        sort_by = st.selectbox(
            "Sort by",
            ["Ticker", "Stage Reached", "Moat Score", "MoS %"],
            key="pipeline_sort",
        )

    # Apply filters
    filtered = candidates
    if status_filter:
        filtered = [
            c
            for c in filtered
            if _STATUS_LABELS.get(c.get("pipeline_status", ""), "Unknown") in status_filter
        ]
    if sector_filter:
        filtered = [c for c in filtered if c.get("sector", "Unknown") in sector_filter]

    # Sort
    if sort_by == "Stage Reached":
        stage_rank = {s: i for i, s in enumerate(PIPELINE_STAGE_ORDER)}
        filtered.sort(
            key=lambda c: stage_rank.get(c.get("furthest_stage", ""), -1),
            reverse=True,
        )
    elif sort_by == "Moat Score":
        filtered.sort(key=lambda c: _safe_float(c.get("moat_score")), reverse=True)
    elif sort_by == "MoS %":
        filtered.sort(
            key=lambda c: _safe_float(c.get("margin_of_safety_pct")),
            reverse=True,
        )
    else:
        filtered.sort(key=lambda c: c.get("ticker", ""))

    # ── Candidates table ─────────────────────────────────────────────
    _render_candidates_table(filtered, held, pending)


def _render_funnel(candidates: list[dict]) -> None:
    """Render a pipeline funnel chart showing how many pass each stage."""
    total = len(candidates)
    stage_counts: dict[str, int] = {}
    for stage in PIPELINE_STAGE_ORDER:
        stage_counts[stage] = sum(
            1
            for c in candidates
            if stage in c.get("stages", {}) and c["stages"][stage].get("passed") is True
        )

    labels = [
        f"Screened ({total})",
        f"Quant Pass ({stage_counts.get('quant_screen', 0)})",
        f"Moat Pass ({stage_counts.get('moat_analysis', 0)})",
        f"Mgmt Pass ({stage_counts.get('management_quality', 0)})",
        f"IV Pass ({stage_counts.get('intrinsic_value', 0)})",
        f"Thesis ({stage_counts.get('thesis_generator', 0)})",
    ]
    values = [
        total,
        stage_counts.get("quant_screen", 0),
        stage_counts.get("moat_analysis", 0),
        stage_counts.get("management_quality", 0),
        stage_counts.get("intrinsic_value", 0),
        stage_counts.get("thesis_generator", 0),
    ]

    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=values,
            textinfo="value+percent initial",
            marker={
                "color": [
                    ACCENT_BLUE,
                    ACCENT_BLUE,
                    ACCENT_GREEN,
                    ACCENT_GREEN,
                    ACCENT_GREEN,
                    ACCENT_GREEN,
                ],
            },
        )
    )
    fig.update_layout(
        template=get_chart_template(),
        height=350,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
    )
    st.plotly_chart(fig, width="stretch")


def _render_candidates_table(candidates: list[dict], held: set[str], pending: set[str]) -> None:
    """Render the full candidates table with stage progress."""
    if not candidates:
        st.info("No candidates match the selected filters.")
        return

    rows = []
    for c in candidates:
        ticker = c.get("ticker", "").upper()
        stages = c.get("stages", {})
        status = _STATUS_LABELS.get(c.get("pipeline_status", ""), "Unknown")
        furthest = _STAGE_DISPLAY.get(c.get("furthest_stage", ""), "---")

        # Stage pass/fail indicators
        def _stage_indicator(stage_key: str) -> str:
            if stage_key not in stages:
                return "---"
            return "Pass" if stages[stage_key].get("passed") else "Fail"

        # Trading status
        if ticker in held:
            trade_status = "Held"
        elif ticker in pending:
            trade_status = "Pending"
        else:
            trade_status = "---"

        failure_reasons = _extract_failure_reasons(c)

        rows.append(
            {
                "Ticker": ticker,
                "Sector": c.get("sector", "Unknown"),
                "Furthest Stage": furthest,
                "Status": status,
                "Quant": _stage_indicator("quant_screen"),
                "Moat": _stage_indicator("moat_analysis"),
                "Mgmt": _stage_indicator("management_quality"),
                "IV": _stage_indicator("intrinsic_value"),
                "Thesis": _stage_indicator("thesis_generator"),
                "Moat Score": _safe_float(c.get("moat_score")) or None,
                "Mgmt Score": _safe_float(c.get("management_score")) or None,
                "MoS (%)": fmt_pct(_safe_float(c.get("margin_of_safety_pct")))
                if c.get("margin_of_safety_pct")
                else "---",
                "Failed Criteria": "; ".join(failure_reasons) if failure_reasons else "---",
                "Trading": trade_status,
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True, height=500)

    # ── Detail expanders for candidates with stage data ──────────────
    st.subheader("Candidate Details")
    tickers = [r["Ticker"] for r in rows]
    if tickers:
        selected_ticker = st.selectbox(
            "Select a candidate for details",
            tickers,
            key="candidate_detail_select",
        )
        cand = next((c for c in candidates if c.get("ticker", "").upper() == selected_ticker), None)
        if cand:
            _render_candidate_detail(cand)


def _render_candidate_detail(candidate: dict) -> None:
    """Render detailed stage-by-stage breakdown for a single candidate."""
    stages = candidate.get("stages", {})

    for stage_key in PIPELINE_STAGE_ORDER:
        if stage_key not in stages:
            continue
        stage = stages[stage_key]
        passed = stage.get("passed")
        result = stage.get("result", {})
        label = _STAGE_DISPLAY.get(stage_key, stage_key)
        icon = "Pass" if passed else "Fail"

        with st.expander(f"{label}: {icon}", expanded=not passed):
            if stage_key == "quant_screen":
                cols = st.columns(3)
                metrics = [
                    ("P/E", "pe", 1),
                    ("P/B", "pb", 2),
                    ("D/E", "debt_equity", 2),
                    ("ROIC (10y)", "roic_10y_avg", 1),
                    ("FCF Years", "positive_fcf_years", 0),
                    ("Piotroski", "piotroski_score", 0),
                ]
                for i, (name, key, decimals) in enumerate(metrics):
                    val = _safe_float(result.get(key))
                    display = f"{val:.{decimals}f}" if decimals else str(int(val))
                    if key == "roic_10y_avg":
                        display = f"{val * 100:.1f}%"
                    with cols[i % 3]:
                        st.metric(name, display)
            elif stage_key in ("moat_analysis", "management_quality"):
                cols = st.columns(3)
                score_key = "moat_score" if stage_key == "moat_analysis" else "management_score"
                with cols[0]:
                    st.metric("Score", _safe_float(result.get(score_key)))
                if result.get("reasoning"):
                    st.markdown(f"**Reasoning:** {result['reasoning'][:500]}")
            elif stage_key == "intrinsic_value":
                cols = st.columns(4)
                with cols[0]:
                    st.metric(
                        "Intrinsic Value",
                        fmt_currency(result.get("intrinsic_value_per_share"), decimals=2)
                        if result.get("intrinsic_value_per_share")
                        else "---",
                    )
                with cols[1]:
                    st.metric(
                        "DCF",
                        fmt_currency(result.get("dcf_per_share"), decimals=2)
                        if result.get("dcf_per_share")
                        else "---",
                    )
                with cols[2]:
                    st.metric(
                        "EPV",
                        fmt_currency(result.get("epv_per_share"), decimals=2)
                        if result.get("epv_per_share")
                        else "---",
                    )
                with cols[3]:
                    mos = _safe_float(result.get("margin_of_safety"))
                    st.metric("MoS", f"{mos * 100:.1f}%" if mos else "---")
            elif stage_key == "thesis_generator":
                if result.get("thesis_s3_key"):
                    st.info(f"Thesis stored at: `{result['thesis_s3_key']}`")


def _render_pipeline(candidates: list[dict], held: set[str], pending: set[str]) -> None:
    """Render Oracle pipeline candidates with trading status (watchlist only)."""
    if not candidates:
        st.info(
            "No pipeline candidates found. The Oracle analysis pipeline "
            "populates this view when candidates are screened."
        )
        return

    # Hero metrics
    held_candidates = [c for c in candidates if c.get("ticker", "").upper() in held]
    pending_candidates = [c for c in candidates if c.get("ticker", "").upper() in pending]
    mos_values = [
        c.get("margin_of_safety_pct", 0) for c in candidates if c.get("margin_of_safety_pct")
    ]
    avg_mos = sum(mos_values) / len(mos_values) if mos_values else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Pipeline Candidates", len(candidates))
    with c2:
        st.metric("Currently Held", len(held_candidates))
    with c3:
        st.metric("Pending Orders", len(pending_candidates))
    with c4:
        st.metric("Avg Margin of Safety", fmt_pct(avg_mos))

    st.divider()

    rows = []
    for c in candidates:
        ticker = c.get("ticker", "").upper()
        if ticker in held:
            status = "Held"
        elif ticker in pending:
            status = "Pending"
        else:
            status = "Not Traded"

        rows.append(
            {
                "Ticker": ticker,
                "Sector": c.get("sector", "Unknown"),
                "Moat": c.get("moat_score", "\u2014"),
                "Mgmt": c.get("management_score", "\u2014"),
                "IV": fmt_currency(c.get("intrinsic_value_per_share"), decimals=2)
                if c.get("intrinsic_value_per_share")
                else "\u2014",
                "Price": fmt_currency(c.get("current_price"), decimals=2)
                if c.get("current_price")
                else "\u2014",
                "MoS (%)": fmt_pct(c.get("margin_of_safety_pct", 0)),
                "Status": status,
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

    # ── Radar chart comparator ───────────────────────────────────────
    tickers_with_data = [
        c.get("ticker", "").upper()
        for c in candidates
        if c.get("moat_score") and c.get("management_score")
    ]

    if len(tickers_with_data) >= 2:
        st.subheader("Compare Candidates")
        selected = st.multiselect(
            "Select up to 4 tickers",
            tickers_with_data,
            default=tickers_with_data[:2],
            max_selections=4,
            key="pipeline_compare",
        )

        if selected:
            fig = go.Figure()
            categories = ["Moat", "Mgmt", "MoS", "Piotroski"]

            for ticker in selected:
                cand = next(
                    (c for c in candidates if c.get("ticker", "").upper() == ticker),
                    None,
                )
                if not cand:
                    continue

                values = [
                    cand.get("moat_score", 0) or 0,
                    cand.get("management_score", 0) or 0,
                    min((cand.get("margin_of_safety_pct", 0) or 0) / 10, 10),
                    (cand.get("piotroski_f_score", 0) or 0) * 10 / 9,
                ]
                values.append(values[0])  # close the polygon

                fig.add_trace(
                    go.Scatterpolar(
                        r=values,
                        theta=categories + [categories[0]],
                        name=ticker,
                        fill="toself",
                        opacity=0.6,
                    )
                )

            fig.update_layout(
                template=get_chart_template(),
                polar={"radialaxis": {"visible": True, "range": [0, 10]}},
                height=400,
            )
            st.plotly_chart(fig, width="stretch")


def _render_watchlists() -> None:
    """Render Alpaca watchlist management."""
    from dashboard.watchlist_manager import (
        add_symbol,
        create_watchlist,
        delete_watchlist,
        fetch_quotes,
        get_watchlists,
        remove_symbol,
    )

    client = get_alpaca_client()

    try:
        watchlists = get_watchlists(client)
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    # Create new watchlist
    with st.expander("Create Watchlist"):
        with st.form("create_wl"):
            name = st.text_input("Name", placeholder="e.g. Value Picks")
            symbols_str = st.text_input("Symbols (comma-separated)", placeholder="AAPL, MSFT")
            if st.form_submit_button("Create"):
                try:
                    syms = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
                    create_watchlist(client, name, syms or None)
                    st.success(f"Created watchlist '{name}'")
                    st.rerun()
                except (ValueError, Exception) as exc:
                    st.error(str(exc))

    if not watchlists:
        st.info("No watchlists. Create one above.")
        return

    # Select watchlist
    selected_wl = st.selectbox(
        "Select Watchlist",
        watchlists,
        format_func=lambda w: f"{w.name} ({len(w.symbols)} symbols)",
        key="wl_select",
    )

    if selected_wl:
        # Symbols and quotes
        if selected_wl.symbols:
            quotes = fetch_quotes(selected_wl.symbols, ttl_seconds=30)
            rows = []
            for sym in selected_wl.symbols:
                q = quotes.get(sym, {})
                rows.append(
                    {
                        "Symbol": sym,
                        "Price": fmt_currency(q.get("price"), decimals=2)
                        if q.get("price")
                        else "\u2014",
                        "Change": f"{q.get('change_pct', 0):+.2f}%"
                        if q.get("change_pct") is not None
                        else "\u2014",
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

            # Remove symbol
            sym_to_remove = st.selectbox(
                "Remove symbol",
                selected_wl.symbols,
                key="wl_remove_sym",
            )
            if st.button("Remove", key="wl_remove_btn"):
                try:
                    remove_symbol(client, selected_wl.watchlist_id, sym_to_remove)
                    st.success(f"Removed {sym_to_remove}")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.info("This watchlist has no symbols.")

        # Add symbol
        col_add, col_del = st.columns(2)
        with col_add:
            new_sym = st.text_input("Add symbol", key="wl_add_sym", placeholder="AAPL")
            if st.button("Add", key="wl_add_btn"):
                if new_sym.strip():
                    try:
                        add_symbol(client, selected_wl.watchlist_id, new_sym)
                        st.success(f"Added {new_sym.upper()}")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

        with col_del:
            if st.button("Delete Watchlist", key="wl_delete_btn", type="secondary"):
                try:
                    delete_watchlist(client, selected_wl.watchlist_id)
                    st.success(f"Deleted '{selected_wl.name}'")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def _render_signals() -> None:
    """Render recent Oracle BUY/SELL signals."""
    try:
        decisions = _fetch_recent_decisions()
    except (DataLoadError, Exception) as exc:
        st.error(f"Failed to load recent signals: {exc}")
        decisions = []

    if not decisions:
        st.info("No recent signals from the Oracle analysis pipeline.")
        return

    for d in decisions:
        signal = d.get("signal", "UNKNOWN")
        ticker = d.get("ticker", "Unknown")
        timestamp = fmt_date(d.get("timestamp"))

        if signal == "BUY":
            icon = ":material/trending_up:"
        elif signal == "SELL":
            icon = ":material/trending_down:"
        else:
            icon = ":material/remove:"

        with st.expander(f"{icon} {signal} {ticker} - {timestamp}"):
            # Show reasons
            reasons = d.get("reasons_pass") or d.get("reasons_fail") or []
            if reasons:
                for reason in reasons:
                    st.markdown(f"- {reason}")

            # Show key metrics if available
            m1, m2, m3 = st.columns(3)
            with m1:
                if d.get("moat_score"):
                    st.metric("Moat Score", d["moat_score"])
            with m2:
                if d.get("management_score"):
                    st.metric("Mgmt Score", d["management_score"])
            with m3:
                if d.get("margin_of_safety_pct"):
                    st.metric("MoS", fmt_pct(d["margin_of_safety_pct"]))


# ── Run Pipeline tab ─────────────────────────────────────────────────────


def _render_run_pipeline() -> None:
    """Render the pipeline execution UI: config form, progress, or results."""
    # Determine state
    is_running = st.session_state.get("ps_running", False)
    has_results = "ps_results" in st.session_state

    if is_running:
        _render_search_progress()
    elif has_results:
        _render_search_results(st.session_state["ps_results"])
    else:
        _render_search_config()


def _render_search_config() -> None:
    """Render the search configuration form."""
    from dashboard.search_config import SearchConfig
    from dashboard.search_runner import ThreadSafeProgress

    st.subheader("Run Oracle Pipeline")
    st.markdown(
        "Search the market for value investment candidates using the Oracle's "
        "3-tier screening funnel: Yahoo Finance screener, composite scoring, "
        "then full pipeline analysis (quant, moat, management, intrinsic value, thesis)."
    )

    # Previously-evaluated ticker info
    try:
        from shared.evaluated_store import EvaluatedTickerStore

        eval_store = EvaluatedTickerStore()
        eval_count = eval_store.get_evaluation_count()
    except Exception:
        eval_count = 0

    if eval_count > 0:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.info(
                f"Skipping **{eval_count}** previously evaluated companies "
                f"(auto-reset after 90 days)."
            )
        with col2:
            if st.button("Reset List", help="Clear the evaluated list to re-scan all companies"):
                eval_store.clear_all()
                st.success("Evaluated list cleared.")
                st.rerun()

    # LLM budget pre-check
    budget_warning = _check_llm_budget()
    if budget_warning == "exhausted":
        st.error(
            "LLM budget exhausted for this month. Pipeline analysis stages "
            "that require Claude will be skipped. Consider waiting until "
            "next month or increasing the budget."
        )
    elif budget_warning == "low":
        st.warning("LLM budget is running low. Search may be limited.")

    with st.form("pipeline_search_config"):
        num_results = st.slider(
            "Qualifying companies to find",
            min_value=1,
            max_value=10,
            value=3,
            help="Search stops after finding this many companies passing all quality gates.",
        )
        time_limit = st.slider(
            "Time limit (minutes)",
            min_value=5,
            max_value=60,
            value=15,
            help="Maximum search duration. Longer allows more candidates to be evaluated.",
        )
        include_web = st.checkbox(
            "Include web-sourced candidates",
            value=True,
            help=(
                "Merge tickers from Firecrawl-scraped sources "
                "(Finviz, SEC filings, insider feeds, analyst upgrades, etc.) "
                "into the candidate pool alongside Yahoo Finance screener results."
            ),
        )
        re_evaluate = st.checkbox(
            "Include previously evaluated tickers",
            value=False,
            help="Re-scan companies evaluated in the last 90 days instead of skipping them.",
        )
        submitted = st.form_submit_button(
            "Start Search",
            type="primary",
            width="stretch",
        )

    if submitted:
        config = SearchConfig(
            num_results=num_results,
            time_limit_minutes=time_limit,
            include_web_sources=include_web,
            re_evaluate=re_evaluate,
        )
        progress = ThreadSafeProgress()
        cancel_event = threading.Event()

        st.session_state["ps_running"] = True
        st.session_state["ps_progress"] = progress
        st.session_state["ps_cancel"] = cancel_event

        thread = threading.Thread(
            target=_search_thread_target,
            args=(config, progress, cancel_event),
            daemon=True,
        )
        st.session_state["ps_thread"] = thread
        thread.start()
        st.rerun()


def _search_thread_target(
    config: object,
    progress: object,
    cancel_event: threading.Event,
) -> None:
    """Background thread target that runs the search."""
    from dashboard.search_runner import run_search

    try:
        results = run_search(config, progress, cancel_event)  # type: ignore[arg-type]
        progress.update({"final_results": results})  # type: ignore[union-attr]
    except Exception as exc:
        progress.update(
            {  # type: ignore[union-attr]
                "is_complete": True,
                "error": f"Search failed: {exc}",
            }
        )


def _render_search_progress() -> None:
    """Render live progress of a running search."""
    from streamlit_autorefresh import st_autorefresh

    progress = st.session_state.get("ps_progress")
    if progress is None:
        st.session_state["ps_running"] = False
        st.rerun()
        return

    snap = progress.snapshot()

    # Auto-refresh while running
    if not snap.get("is_complete"):
        st_autorefresh(interval=2000, key="ps_autorefresh")

    # Hero metrics
    web_count = snap.get("web_candidate_count", 0)
    cols = st.columns(5 if web_count else 4)
    with cols[0]:
        st.metric("Evaluated", snap.get("evaluated_count", 0))
    with cols[1]:
        st.metric("Matches", snap.get("match_count", 0))
    with cols[2]:
        elapsed = snap.get("elapsed_seconds", 0)
        st.metric("Elapsed", f"{elapsed / 60:.1f} min")
    with cols[3]:
        st.metric("Screener", snap.get("screener_count", 0))
    if web_count:
        with cols[4]:
            st.metric("Web Sources", web_count)

    # Status
    action = snap.get("current_action", "")
    ticker = snap.get("current_ticker", "")
    if action:
        st.info(f"{action}" + (f" ({ticker})" if ticker and ticker not in action else ""))

    # Error from abort
    if snap.get("error"):
        st.error(snap["error"])

    # Warning
    if snap.get("warning"):
        st.warning(snap["warning"])

    # Running results table
    results = snap.get("results", [])
    if results:
        _render_results_table(results)

    # Cancel / Complete
    if snap.get("is_complete"):
        final = snap.get("final_results") or results
        st.session_state["ps_results"] = final
        st.session_state["ps_running"] = False
        # Clean up
        for key in ("ps_progress", "ps_cancel", "ps_thread"):
            st.session_state.pop(key, None)
        # Invalidate cached data so other tabs show fresh results
        _fetch_all_candidates.clear()
        _fetch_oracle_candidates.clear()
        _fetch_run_dates.clear()
        _fetch_recent_decisions.clear()
        st.rerun()
    else:
        if st.button("Cancel Search", key="ps_cancel_btn", type="secondary"):
            cancel = st.session_state.get("ps_cancel")
            if cancel:
                cancel.set()
            st.info("Cancelling...")


def _render_search_results(results: list) -> None:
    """Render completed search results."""
    st.subheader("Search Results")

    qualifiers = [r for r in results if r.passed_all_gates]
    near_misses = [r for r in results if not r.passed_all_gates and r.gates_passed_count >= 2]

    # Summary
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total Evaluated", len(results))
    with c2:
        st.metric("Qualifiers", len(qualifiers))
    with c3:
        st.metric("Near Misses", len(near_misses))

    st.divider()

    # Qualifiers
    if qualifiers:
        st.markdown("### Qualifying Companies")
        for q in qualifiers:
            with st.expander(f"{q.ticker} — {q.company_name}", expanded=True):
                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    st.metric("Moat Score", q.moat_score)
                with m2:
                    st.metric("Mgmt Score", q.management_score)
                with m3:
                    st.metric("MoS", f"{q.margin_of_safety * 100:.1f}%")
                with m4:
                    st.metric(
                        "Intrinsic Value",
                        fmt_currency(q.intrinsic_value, decimals=2) if q.intrinsic_value else "---",
                    )
    else:
        st.info("No companies passed all quality gates in this search.")

    # Near misses
    if near_misses:
        st.markdown("### Near Misses (2+ gates passed)")
        for nm in near_misses:
            gates = nm.gate_details
            gate_str = ", ".join(f"{'Pass' if v else 'Fail'} {k}" for k, v in gates.items())
            with st.expander(f"{nm.ticker} — {gate_str}"):
                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Moat", nm.moat_score)
                with m2:
                    st.metric("Mgmt", nm.management_score)
                with m3:
                    st.metric("MoS", f"{nm.margin_of_safety * 100:.1f}%")

    # Full results table
    if results:
        st.markdown("### All Evaluated Companies")
        _render_results_table(results)

    st.info(
        "These results are now stored in DynamoDB and visible in the "
        "**All Scanned Candidates** tab."
    )

    # New search button
    if st.button("New Search", key="ps_new_search", type="primary"):
        st.session_state.pop("ps_results", None)
        st.rerun()


def _render_results_table(results: list) -> None:
    """Render a DataFrame of SearchResult objects."""
    rows = []
    for r in results:
        rows.append(
            {
                "Ticker": r.ticker,
                "Company": r.company_name,
                "Moat": r.moat_score,
                "Mgmt": r.management_score,
                "MoS (%)": f"{r.margin_of_safety * 100:.1f}%",
                "Gates": f"{r.gates_passed_count}/3",
                "Passed": "Yes" if r.passed_all_gates else "No",
                "Error": r.error or "",
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)


def _check_llm_budget() -> str | None:
    """Check LLM budget status. Returns 'exhausted', 'low', or None."""
    try:
        from shared.config import get_config
        from shared.cost_tracker import CostTracker

        cfg = get_config()
        tracker = CostTracker(cfg.table_cost_tracking)
        status = tracker.check_budget()
        spent = float(status.get("spent_usd", 0))
        budget = float(status.get("budget_usd", 50))
        if budget > 0 and spent >= budget:
            return "exhausted"
        if budget > 0 and spent >= budget * 0.8:
            return "low"
    except Exception:
        pass
    return None
