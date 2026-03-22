"""Upload Analysis page — upload an SEC filing and run the Omaha Oracle pipeline."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.analysis_runner import (
    build_analysis_event,
    format_analysis_summary,
    run_upload_analysis,
)
from dashboard.upload_storage import extract_filing_text, store_uploaded_file
from dashboard.upload_validator import UploadMetadata, validate_upload
from shared.logger import get_logger

_log = get_logger(__name__)


def render() -> None:
    """Render the Upload Analysis page."""
    st.title("Upload & Analyze Filing")
    st.caption(
        "Upload an SEC filing (10-K, 10-Q, or annual report) to run the "
        "Omaha Oracle analysis pipeline and view results."
    )

    # ── File uploader ──
    uploaded = st.file_uploader(
        "Choose a financial statement",
        type=["pdf", "xlsx", "xls", "html", "htm"],
        help="Supported formats: PDF, XLSX, HTML. Max 50 MB.",
    )

    # ── Metadata form ──
    with st.form("upload_metadata_form"):
        col1, col2 = st.columns(2)
        with col1:
            ticker = st.text_input("Ticker Symbol", placeholder="e.g. AAPL")
            filing_type = st.selectbox(
                "Filing Type",
                options=["10-K", "10-Q", "Annual Report", "Quarterly Report"],
            )
        with col2:
            company_name = st.text_input("Company Name", placeholder="e.g. Apple Inc.")
            fiscal_year = st.number_input("Fiscal Year", min_value=1900, max_value=2100, value=2025)

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            sector = st.text_input("Sector (optional)", placeholder="e.g. Technology")
        with col_s2:
            industry = st.text_input("Industry (optional)", placeholder="e.g. Consumer Electronics")

        submitted = st.form_submit_button("Run Analysis", use_container_width=True)

    # ── Handle submission ──
    if submitted:
        _handle_submission(
            uploaded, ticker, company_name, filing_type, fiscal_year, sector, industry
        )

    # ── Display stored results ──
    if "upload_result" in st.session_state and st.session_state.upload_result:
        _display_results(st.session_state.upload_result)


def _handle_submission(
    uploaded: Any,
    ticker: str,
    company_name: str,
    filing_type: str,
    fiscal_year: int,
    sector: str,
    industry: str,
) -> None:
    """Validate inputs, store file, run pipeline, and save results to session state."""
    # Validate file
    if uploaded is None:
        st.error("Please upload a file before submitting.")
        return

    errors = validate_upload(uploaded)
    if errors:
        for err in errors:
            st.error(err)
        return

    # Validate metadata
    try:
        metadata = UploadMetadata(
            ticker=ticker,
            company_name=company_name,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
        )
    except Exception as exc:
        st.error(f"Invalid metadata: {exc}")
        return

    # Read file bytes
    file_bytes = uploaded.getvalue()

    # Extract filing context
    filing_context = extract_filing_text(file_bytes, uploaded.name, metadata)

    if "not yet supported" in filing_context.lower():
        st.warning(
            "PDF text extraction is not yet supported. "
            "Analysis will be based on metadata only."
        )

    # Build extra metrics
    extra_metrics: dict[str, Any] = {}
    if sector.strip():
        extra_metrics["sector"] = sector.strip()
    if industry.strip():
        extra_metrics["industry"] = industry.strip()

    # Run pipeline with progress
    with st.status("Running Omaha Oracle analysis pipeline...", expanded=True) as status:

        def _progress(stage_name: str, stage_num: int) -> None:
            status.update(label=f"Stage {stage_num}/4: {stage_name.replace('_', ' ').title()}")

        try:
            s3_key = store_uploaded_file(file_bytes, uploaded.name, metadata)
            event = build_analysis_event(metadata, s3_key, extra_metrics=extra_metrics or None)
            result = run_upload_analysis(event, filing_context, progress_callback=_progress)
            st.session_state.upload_result = result
            status.update(label="Analysis complete!", state="complete")
        except Exception:
            _log.exception("Pipeline failed")
            st.error("Analysis pipeline encountered an error. Please try again or check server logs.")
            status.update(label="Pipeline failed", state="error")


def _display_results(result: dict[str, Any]) -> None:
    """Display analysis results with hero metrics, tabs, and thesis."""
    st.divider()
    summary = format_analysis_summary(result)

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4, col5 = st.columns(5, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric("Moat Score", summary["moat_display"])
    with col2:
        st.metric("Management", summary["management_display"])
    with col3:
        st.metric("Intrinsic Value", summary["iv_display"])
    with col4:
        st.metric("Margin of Safety", summary["mos_display"])
    with col5:
        st.metric("Signal", summary["buy_signal"])

    st.divider()

    # ── Tier 2: Tabs ──
    tab_moat, tab_mgmt, tab_val, tab_thesis = st.tabs(
        ["Moat Analysis", "Management", "Valuation", "Investment Thesis"]
    )

    with tab_moat:
        _render_moat_tab(result)

    with tab_mgmt:
        _render_management_tab(result)

    with tab_val:
        _render_valuation_tab(result)

    with tab_thesis:
        _render_thesis_tab(result)

    # ── Tier 3: Raw data expander ──
    with st.expander("Raw Analysis Data"):
        st.json(result)


def _render_moat_tab(result: dict[str, Any]) -> None:
    moat_score = result.get("moat_score", 0)
    if not moat_score:
        st.info("Moat analysis not available.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Moat Type", result.get("moat_type", "Unknown").title())
    with col2:
        st.metric("Pricing Power", f"{result.get('pricing_power', 0)}/10")
    with col3:
        st.metric("Customer Captivity", f"{result.get('customer_captivity', 0)}/10")

    st.markdown(f"**Reasoning:** {result.get('reasoning', 'N/A')}")

    sources = result.get("moat_sources", [])
    if sources:
        st.markdown("**Moat Sources:** " + ", ".join(sources))

    risks = result.get("risks_to_moat", [])
    if risks:
        st.markdown("**Risks:** " + ", ".join(risks))


def _render_management_tab(result: dict[str, Any]) -> None:
    mgmt_score = result.get("management_score", 0)
    if not mgmt_score:
        st.info("Management assessment not available.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Owner-Operator Mindset", f"{result.get('owner_operator_mindset', 0)}/10")
    with col2:
        st.metric("Capital Allocation", f"{result.get('capital_allocation_skill', 0)}/10")
    with col3:
        st.metric("Candor & Transparency", f"{result.get('candor_transparency', 0)}/10")

    reasoning = result.get("reasoning", "")
    if reasoning:
        st.markdown(f"**Reasoning:** {reasoning}")

    green = result.get("green_flags", [])
    red = result.get("red_flags", [])
    if green:
        st.markdown("**Green Flags:** " + ", ".join(green))
    if red:
        st.markdown("**Red Flags:** " + ", ".join(red))


def _render_valuation_tab(result: dict[str, Any]) -> None:
    iv = result.get("intrinsic_value_per_share")
    if not iv:
        st.info("Valuation data not available.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("DCF / Share", f"${result.get('dcf_per_share', 0):,.2f}")
    with col2:
        st.metric("EPV / Share", f"${result.get('epv_per_share', 0):,.2f}")
    with col3:
        st.metric("Asset Floor", f"${result.get('floor_per_share', 0):,.2f}")

    mos = result.get("margin_of_safety", 0)
    price = result.get("current_price", 0)
    st.markdown(
        f"**Composite Intrinsic Value:** ${iv:,.2f} &nbsp;|&nbsp; "
        f"**Current Price:** ${price:,.2f} &nbsp;|&nbsp; "
        f"**Margin of Safety:** {mos * 100:.1f}%"
    )

    scenarios = result.get("scenarios", {})
    if scenarios:
        st.markdown("**DCF Scenarios:**")
        for label in ("bear", "base", "bull"):
            s = scenarios.get(label, {})
            if s:
                st.markdown(
                    f"- **{label.title()}** ({s.get('growth_pct', '?')}% growth, "
                    f"{s.get('probability', '?'):.0%} prob): "
                    f"${s.get('dcf_per_share', 0):,.2f}/share"
                )


def _render_thesis_tab(result: dict[str, Any]) -> None:
    thesis_key = result.get("thesis_s3_key")
    if not thesis_key:
        generated = result.get("thesis_generated", False)
        if not generated:
            st.info(
                "Investment thesis was not generated. The company may not have "
                "passed all quality gates (moat >= 7, management >= 6, MoS > 30%)."
            )
        return

    st.markdown(f"*Thesis stored at:* `{thesis_key}`")
    st.info("Thesis rendering from S3 will be available in a future update.")
