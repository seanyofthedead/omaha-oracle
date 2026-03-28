"""Company Search page — discover qualifying companies from the SEC universe."""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from dashboard.search_config import SearchConfig
from dashboard.search_runner import SearchResult, run_search


class ThreadSafeDict:
    """A dict-like wrapper guarded by a threading.Lock for safe cross-thread access."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def update(self, other: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(other)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data


def render() -> None:
    """Render the Company Search page."""
    st.title("Company Search")
    st.caption(
        "Discover investment opportunities using a 3-tier screening funnel: "
        "Tier 1 bulk-filters the Yahoo Finance universe by P/E, P/B, D/E, ROE, and FCF; "
        "Tier 2 ranks survivors by composite gate-proximity score; "
        "Tier 3 runs top candidates through the full pipeline "
        "(quant screen, moat \u2265 7, management \u2265 6, MoS > 30%)."
    )

    # Initialize session state keys
    _init_session_state()

    # Check if a search thread is running
    thread: threading.Thread | None = st.session_state.get("search_thread")
    is_running = thread is not None and thread.is_alive()

    if is_running:
        _render_progress()
    else:
        _render_config_form()
        if st.session_state.get("search_results"):
            _render_results(st.session_state.search_results)


def _init_session_state() -> None:
    """Ensure all session state keys exist."""
    defaults = {
        "search_config": None,
        "search_thread": None,
        "search_cancel_event": None,
        "search_progress": {},
        "search_results": None,
        "search_evaluated_tickers": set(),
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _render_evaluated_info() -> None:
    """Show how many tickers have been previously evaluated, with reset option."""
    try:
        from shared.evaluated_store import EvaluatedTickerStore

        store = EvaluatedTickerStore()
        count = store.get_evaluation_count()
    except Exception:
        return  # Table may not exist yet (pre-deploy); silently skip

    if count == 0:
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        st.info(
            f"Skipping **{count}** previously evaluated companies "
            f"(auto-reset after 90 days)."
        )
    with col2:
        if st.button("Reset List", help="Clear the evaluated list to re-scan all companies"):
            store.clear_all()
            st.success("Evaluated list cleared.")
            st.rerun()


def _render_config_form() -> None:
    """Render the search configuration form."""
    _render_evaluated_info()
    if st.session_state.get("search_results"):
        st.warning("Previous results will be cleared when starting a new search.")

    with st.form("search_config_form"):
        num_results = st.slider(
            "Number of qualifying companies to find",
            min_value=1,
            max_value=10,
            value=3,
        )
        time_limit = st.slider(
            "Search time limit (minutes)",
            min_value=5,
            max_value=60,
            value=15,
        )
        submitted = st.form_submit_button("Start Search", width="stretch")

    if submitted:
        config = SearchConfig(num_results=num_results, time_limit_minutes=time_limit)
        st.session_state.search_config = config
        st.session_state.search_results = None
        st.session_state.search_progress = {}

        cancel_event = threading.Event()
        st.session_state.search_cancel_event = cancel_event

        progress: ThreadSafeDict = ThreadSafeDict()
        st.session_state.search_progress = progress

        thread = threading.Thread(
            target=_search_thread_target,
            args=(config, progress, cancel_event),
            daemon=True,
        )
        st.session_state.search_thread = thread
        thread.start()
        st.rerun()


def _search_thread_target(
    config: SearchConfig,
    progress: dict[str, Any],
    cancel_event: threading.Event,
) -> None:
    """Background thread target for running the search."""
    try:
        results = run_search(config, progress, cancel_event)
        progress["final_results"] = results
    except Exception as exc:
        progress["error"] = str(exc)
        progress["is_complete"] = True


def _render_progress() -> None:
    """Render progress display while search is running."""
    progress = st.session_state.get("search_progress", {})

    # Cancel button
    if st.button("Cancel Search", type="secondary"):
        cancel_event = st.session_state.get("search_cancel_event")
        if cancel_event:
            cancel_event.set()

    # Previously evaluated info
    skipped = progress.get("skipped_previously_evaluated", 0)
    if skipped:
        st.caption(f"Skipping {skipped} previously evaluated companies.")

    # Hero metrics
    screener_count = progress.get("screener_count", 0)
    col1, col2, col3, col4, col5 = st.columns(5, gap="large")
    with col1:
        st.metric("Pre-screened", screener_count)
    with col2:
        st.metric("Evaluated", progress.get("evaluated_count", 0))
    with col3:
        st.metric("Matches Found", progress.get("match_count", 0))
    with col4:
        elapsed = progress.get("elapsed_seconds", 0)
        st.metric("Time Elapsed", f"{elapsed:.0f}s")
    with col5:
        config = st.session_state.get("search_config")
        if config:
            remaining = max(0, config.time_limit_minutes * 60 - elapsed)
            st.metric("Time Remaining", f"{remaining:.0f}s")

    # Status
    action = progress.get("current_action", "Initializing...")
    ticker = progress.get("current_ticker", "")
    with st.status(f"{action}", expanded=True):
        if ticker:
            st.write(f"Current ticker: **{ticker}**")

    # Running results table
    results: list[SearchResult] = progress.get("results", [])
    if results:
        _render_results_table(results)

    # Check if thread finished
    thread: threading.Thread | None = st.session_state.get("search_thread")
    if thread and not thread.is_alive():
        # Thread finished — collect results
        final = progress.get("final_results", [])
        if final:
            st.session_state.search_results = final
        elif results:
            st.session_state.search_results = results
        st.session_state.search_thread = None
        st.rerun()
    else:
        # Auto-refresh while running (non-blocking)
        st_autorefresh(interval=2000, key="search_autorefresh")


def _render_results(results: list[SearchResult]) -> None:
    """Render final search results."""
    progress = st.session_state.get("search_progress", {})
    config = st.session_state.get("search_config")

    # Search summary with funnel metrics
    with st.container(border=True):
        st.subheader("Search Summary")
        screener_count = progress.get("screener_count", 0)
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("Pre-screened", screener_count)
        with col2:
            st.metric("Pipeline Evaluated", progress.get("evaluated_count", len(results)))
        with col3:
            matches = [r for r in results if r.passed_all_gates]
            st.metric("Qualifying", len(matches))
        with col4:
            elapsed = progress.get("elapsed_seconds", 0)
            st.metric("Time Taken", f"{elapsed:.1f}s")
        with col5:
            if config:
                st.metric("Target", f"{config.num_results} companies")

        if progress.get("was_cancelled"):
            st.info("Search was cancelled. Showing partial results.")
        if progress.get("candidates_exhausted"):
            st.info("All candidates in the universe have been evaluated.")

    # Qualifiers section
    qualifiers = [r for r in results if r.passed_all_gates]
    if qualifiers:
        st.subheader(f"Qualifying Companies ({len(qualifiers)})")
        for sr in qualifiers:
            _render_qualifier_card(sr)
    else:
        st.info("No qualifying companies found matching all quality gates.")

    # Near misses section
    near_misses = [r for r in results if not r.passed_all_gates and r.gates_passed_count >= 2]
    if near_misses:
        with st.expander(f"Near Misses ({len(near_misses)}) — passed 2 of 3 gates"):
            for sr in near_misses:
                _render_near_miss(sr)

    # All results
    with st.expander("Raw Data"):
        _render_results_table(results)


def _render_qualifier_card(sr: SearchResult) -> None:
    """Render a card for a qualifying company."""
    with st.container(border=True):
        st.markdown(f"### {sr.ticker} — {sr.company_name}")
        col1, col2, col3, col4, col5 = st.columns(5, gap="large")
        with col1:
            st.metric("Moat Score", f"{sr.moat_score}/10")
        with col2:
            st.metric("Management", f"{sr.management_score}/10")
        with col3:
            st.metric("Margin of Safety", f"{sr.margin_of_safety * 100:.1f}%")
        with col4:
            iv_display = f"${sr.intrinsic_value:,.2f}" if sr.intrinsic_value else "\u2014"
            st.metric("Intrinsic Value", iv_display)
        with col5:
            price_display = f"${sr.current_price:,.2f}" if sr.current_price else "\u2014"
            st.metric("Current Price", price_display)

        # Thesis content if available
        thesis_key = sr.raw_result.get("thesis_s3_key")
        if thesis_key:
            st.markdown(f"*Thesis stored at:* `{thesis_key}`")
        elif sr.raw_result.get("thesis_generated"):
            st.success("Investment thesis generated.")

        if sr.error:
            st.warning(f"Note: {sr.error}")


def _render_near_miss(sr: SearchResult) -> None:
    """Render a near-miss company entry."""
    failed_gates = [g for g, passed in sr.gate_details.items() if not passed]
    gate_labels = {"moat": "Moat", "management": "Management", "mos": "Margin of Safety"}
    failed_names = [gate_labels.get(g, g) for g in failed_gates]

    st.markdown(
        f"**{sr.ticker}** — {sr.company_name} &nbsp;|&nbsp; "
        f"Moat: {sr.moat_score}/10, Mgmt: {sr.management_score}/10, "
        f"MoS: {sr.margin_of_safety * 100:.1f}% &nbsp;|&nbsp; "
        f"Failed: {', '.join(failed_names)}"
    )


def _render_results_table(results: list[SearchResult]) -> None:
    """Render a dataframe of all evaluated results."""
    if not results:
        return

    rows = []
    for r in results:
        status = "Error" if r.error else ("Pass" if r.passed_all_gates else "Fail")
        rows.append(
            {
                "Ticker": r.ticker,
                "Company": r.company_name,
                "Moat": r.moat_score,
                "Mgmt": r.management_score,
                "MoS %": f"{r.margin_of_safety * 100:.1f}",
                "Status": status,
                "Gates": f"{r.gates_passed_count}/3",
            }
        )

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
