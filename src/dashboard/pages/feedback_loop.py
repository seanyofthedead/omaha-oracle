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
    st.title("Feedback Loop")

    # Active lessons
    st.subheader("Active Lessons")
    lessons = load_lessons()
    if lessons:
        rows = []
        for l in lessons:
            rows.append({
                "Type": l.get("lesson_type", ""),
                "ID": l.get("lesson_id", ""),
                "Severity": l.get("severity", ""),
                "Ticker": l.get("ticker", "") or "—",
                "Sector": l.get("sector", "ALL"),
                "Expires": (l.get("expires_at") or "")[:10],
                "Text": (l.get("prompt_injection_text") or l.get("description", ""))[:60] + "...",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No active lessons.")

    # Confidence calibration
    st.subheader("Confidence Calibration (by stage)")
    conf_lessons = [l for l in lessons if l.get("lesson_type") == "confidence_calibration"]
    if conf_lessons:
        for l in conf_lessons:
            cal = l.get("confidence_calibration") or {}
            stage = cal.get("analysis_stage", "")
            sector = cal.get("sector", "ALL")
            factor = cal.get("adjustment_factor", "")
            st.markdown(f"- **{stage}** / {sector}: `{factor}`")
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
        import pandas as pd

        rates = []
        for k in keys[:12]:
            pm = load_postmortem(k)
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
