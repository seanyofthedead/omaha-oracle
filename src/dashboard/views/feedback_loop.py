"""Feedback Loop page — lessons, calibration, threshold history."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data import (
    DataLoadError,
    load_config_thresholds,
    load_lessons,
    load_postmortem,
    load_postmortem_keys,
)
from dashboard.fmt import fmt_date, fmt_null, fmt_pct_ratio


def render() -> None:
    """Render the Feedback Loop page."""
    st.title("Feedback Loop")
    st.caption(
        "Self-improvement engine — the system audits its own "
        "predictions quarterly, extracts lessons from mistakes, "
        "and injects them into future analysis."
    )

    # Load data for hero row and tabs (multi-step)
    status = st.status("Loading feedback loop data...", expanded=False)

    try:
        status.update(label="Fetching active lessons from DynamoDB...")
        lessons = load_lessons()
    except DataLoadError as exc:
        status.update(label="Failed to load lessons", state="error")
        st.error(str(exc))
        return

    try:
        status.update(label="Listing postmortem archive from S3...")
        keys = load_postmortem_keys()
    except DataLoadError as exc:
        status.update(label="Failed to load postmortems", state="error")
        st.error(str(exc))
        keys = []

    # Hero metric row
    n_lessons = len(lessons)
    conf_count = sum(
        1 for lesson in lessons if lesson.get("lesson_type") == "confidence_calibration"
    )
    n_postmortems = len(keys)

    # Get latest mistake rate from most recent postmortem
    latest_mr = fmt_null(None)
    if keys:
        try:
            status.update(label="Reading latest postmortem for mistake rate...")
            pm = load_postmortem(keys[0])
            summary = pm.get("audit_summary") or {}
            mr = summary.get("mistake_rate")
            if mr is not None:
                if isinstance(mr, float) and mr <= 1:
                    latest_mr = fmt_pct_ratio(mr)
                else:
                    latest_mr = f"{mr}%"
        except DataLoadError:
            latest_mr = "err"

    status.update(label="Feedback loop data loaded", state="complete")

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4 = st.columns(4, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric(
            "Active Lessons",
            n_lessons,
            help="Learned rules currently injected into AI "
            "prompts. Extracted from postmortem reviews "
            "and expire over time.",
        )
    with col2:
        st.metric(
            "Calibrations",
            conf_count,
            help="Active adjustments to AI confidence scores. "
            "If the system was overconfident in a sector, "
            "calibrations dampen future scores there.",
        )
    with col3:
        st.metric(
            "Postmortems",
            n_postmortems,
            help="Quarterly self-audits comparing predictions "
            "to actual outcomes. Each generates new lessons.",
        )
    with col4:
        st.metric(
            "Latest Mistake Rate",
            latest_mr,
            help="Percentage of predictions the system got "
            "wrong in the most recent quarterly review. "
            "Trending toward zero is the goal.",
        )

    st.divider()

    # ── Tier 2: Primary content in tabs ──
    tab_lessons, tab_trends, tab_calibration = st.tabs(
        ["Lessons", "Mistake Rate Trend", "Calibration"]
    )

    with tab_lessons:
        if lessons:
            rows = []
            for lesson in lessons:
                text = lesson.get("prompt_injection_text") or lesson.get("description") or ""
                rows.append(
                    {
                        "Type": lesson.get("lesson_type", ""),
                        "ID": lesson.get("lesson_id", ""),
                        "Severity": lesson.get("severity", ""),
                        "Ticker": fmt_null(lesson.get("ticker", "") or None),
                        "Sector": lesson.get("sector", "ALL"),
                        "Times Injected": lesson.get("times_injected", 0),
                        "Expires": fmt_date(lesson.get("expires_at")),
                        "Text": text or fmt_null(None),
                    }
                )
            lesson_df = pd.DataFrame(rows)
            lesson_col_config = {
                "Type": st.column_config.TextColumn("Type", width="small"),
                "ID": st.column_config.TextColumn("ID", width="small"),
                "Severity": st.column_config.TextColumn("Severity", width="small"),
                "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                "Sector": st.column_config.TextColumn("Sector", width="small"),
                "Times Injected": st.column_config.NumberColumn(
                    "Times Injected",
                    width="small",
                    help="Number of times this lesson has been injected into AI analysis prompts.",
                ),
                "Expires": st.column_config.TextColumn("Expires", width="small"),
                "Text": st.column_config.TextColumn(
                    "Text",
                    width="large",
                    help="Full lesson text injected into AI prompts during analysis.",
                ),
            }
            with st.container(border=True):
                st.dataframe(
                    lesson_df,
                    column_config=lesson_col_config,
                    use_container_width=True,
                    hide_index=True,
                    height=min(len(rows) * 35 + 38, 400),
                )
        else:
            st.info(
                "No active lessons. Lessons are extracted from "
                "quarterly postmortem reviews and expire over "
                "time."
            )

    with tab_trends:
        if keys:
            from concurrent.futures import ThreadPoolExecutor

            batch = keys[:12]
            try:
                with st.spinner(f"Loading {len(batch)} quarterly postmortems..."):
                    with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                        postmortems = list(ex.map(load_postmortem, batch))
            except DataLoadError as exc:
                st.error(str(exc))
                postmortems = []

            rates = []
            for postmortem, k in zip(postmortems, batch):
                summary = postmortem.get("audit_summary") or {}
                q = postmortem.get("quarter", k)
                rate = summary.get("mistake_rate", 0)
                rates.append({"quarter": q, "mistake_rate": rate})
            if rates:
                import plotly.graph_objects as go

                from dashboard.charts import (
                    ACCENT_RED,
                    MUTED_GRAY,
                )

                with st.container(border=True):
                    df = pd.DataFrame(rates)
                    fig = go.Figure(
                        go.Scatter(
                            x=df["quarter"],
                            y=df["mistake_rate"],
                            mode="lines+markers",
                            fill="tozeroy",
                            line={
                                "color": ACCENT_RED,
                                "width": 2,
                            },
                            marker={"size": 6},
                            hovertemplate=("<b>%{x}</b><br>Mistake rate: %{y:.1%}<extra></extra>"),
                        )
                    )
                    fig.add_hline(
                        y=0,
                        line_dash="dash",
                        line_color=MUTED_GRAY,
                        annotation_text="Target",
                        annotation_position="top left",
                        annotation_font_color=MUTED_GRAY,
                    )
                    fig.update_layout(
                        title=("Mistake Rate by Quarter — Trending Toward Zero"),
                        xaxis_title=None,
                        yaxis_title=None,
                        yaxis_tickformat=".0%",
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption(
                        "Each point is one quarterly review. "
                        "A declining trend means the system is "
                        "learning from past mistakes and making "
                        "better predictions over time."
                    )
            else:
                st.info("Postmortem files exist but contain no mistake rate data.")
        else:
            st.info(
                "No postmortem reviews on record yet. The first "
                "review runs automatically after one full quarter "
                "of trading."
            )

    with tab_calibration:
        with st.popover("How does confidence calibration work?"):
            st.markdown(
                "After each quarterly review, the system "
                "compares its confidence scores to actual "
                "outcomes by pipeline stage and sector.\n\n"
                "If the AI was systematically overconfident "
                "(e.g., 80% confident but only right 60% of "
                "the time), it creates an **adjustment "
                "factor** that scales down future confidence "
                "scores for that stage/sector combination.\n\n"
                "A factor of **0.75** means 'multiply your "
                "confidence by 0.75 before acting on it.'"
            )
        conf_lessons = [
            lesson for lesson in lessons if lesson.get("lesson_type") == "confidence_calibration"
        ]
        if conf_lessons:
            cal_rows = [
                {
                    "Stage": cal.get("analysis_stage", ""),
                    "Sector": cal.get("sector", "ALL"),
                    "Factor": cal.get("adjustment_factor", ""),
                }
                for lesson in conf_lessons
                if (cal := lesson.get("confidence_calibration") or {})
            ]
            cal_df = pd.DataFrame(cal_rows)
            cal_col_config = {
                "Stage": st.column_config.TextColumn("Stage"),
                "Sector": st.column_config.TextColumn("Sector"),
                "Factor": st.column_config.NumberColumn(
                    "Factor",
                    format="%.2f",
                    help="Multiplier applied to AI confidence. "
                    "< 1.0 means the system was overconfident.",
                ),
            }
            with st.container(border=True):
                st.dataframe(
                    cal_df,
                    column_config=cal_col_config,
                    use_container_width=True,
                    hide_index=True,
                    height=min(len(cal_rows) * 35 + 38, 300),
                )
        else:
            st.info(
                "No confidence calibration adjustments active. "
                "Calibrations are derived from postmortem "
                "analysis of prediction accuracy by stage and "
                "sector."
            )

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Screening Thresholds"):
        try:
            thresholds = load_config_thresholds()
            if thresholds:
                thresh_rows = [{"Metric": k, "Value": v} for k, v in sorted(thresholds.items())]
                st.dataframe(
                    pd.DataFrame(thresh_rows),
                    column_config={
                        "Metric": st.column_config.TextColumn("Metric"),
                        "Value": st.column_config.TextColumn("Value"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    height=min(len(thresh_rows) * 35 + 38, 400),
                )
            else:
                st.info("No threshold overrides configured. Using default screening thresholds.")
        except DataLoadError as exc:
            st.error(str(exc))
