"""Feedback Loop page — lessons, calibration, threshold history."""

from __future__ import annotations

import streamlit as st

from dashboard.data import (
    load_config_thresholds,
    load_lessons,
    load_postmortem,
    load_postmortem_keys,
)


def render() -> None:
    """Render the Feedback Loop page."""
    st.title("Feedback Loop")

    # Active lessons
    st.subheader("Active Lessons")
    lessons = load_lessons()
    if lessons:
        rows = []
        for lesson in lessons:
            rows.append(
                {
                    "Type": lesson.get("lesson_type", ""),
                    "ID": lesson.get("lesson_id", ""),
                    "Severity": lesson.get("severity", ""),
                    "Ticker": lesson.get("ticker", "") or "—",
                    "Sector": lesson.get("sector", "ALL"),
                    "Expires": (lesson.get("expires_at") or "")[:10],
                    "Text": (lesson.get("prompt_injection_text") or lesson.get("description", ""))[
                        :60
                    ]
                    + "...",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No active lessons.")

    # Confidence calibration
    st.subheader("Confidence Calibration (by stage)")
    conf_lessons = [
        lesson for lesson in lessons if lesson.get("lesson_type") == "confidence_calibration"
    ]
    if conf_lessons:
        rows = [
            {
                "Stage": cal.get("analysis_stage", ""),
                "Sector": cal.get("sector", "ALL"),
                "Factor": cal.get("adjustment_factor", ""),
            }
            for lesson in conf_lessons
            if (cal := lesson.get("confidence_calibration") or {})
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No confidence calibration lessons.")

    # Threshold adjustment history
    st.subheader("Screening Thresholds")
    thresholds = load_config_thresholds()
    if thresholds:
        st.json(thresholds)
    else:
        st.info("No threshold config.")

    # Postmortem archive — mistake rate trend
    st.subheader("Mistake Rate Trend")
    keys = load_postmortem_keys()
    if keys:
        from concurrent.futures import ThreadPoolExecutor

        import pandas as pd

        batch = keys[:12]
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            postmortems = list(ex.map(load_postmortem, batch))

        rates = []
        for pm, k in zip(postmortems, batch):
            summary = pm.get("audit_summary") or {}
            q = pm.get("quarter", k)
            mr = summary.get("mistake_rate", 0)
            rates.append({"quarter": q, "mistake_rate": mr})
        if rates:
            df = pd.DataFrame(rates)
            st.line_chart(df.set_index("quarter")["mistake_rate"])
        else:
            st.info("No postmortem data.")
    else:
        st.info("No postmortems on record.")
