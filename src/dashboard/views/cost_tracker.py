"""Cost Tracker page — monthly spend and budget."""

from __future__ import annotations

import streamlit as st

from dashboard.data import load_cost_data


def render() -> None:
    """Render the Cost Tracker page."""
    st.title("Cost Tracker")

    months = st.sidebar.slider("Months to show", 3, 24, 12)
    history, status = load_cost_data(months=months)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Budget", f"${status.get('budget_usd', 0):,.0f}")
    with col2:
        st.metric("Spent", f"${status.get('spent_usd', 0):,.2f}")
    with col3:
        st.metric("Remaining", f"${status.get('remaining_usd', 0):,.2f}")
    with col4:
        util = status.get("utilization_pct", 0)
        st.metric("Utilization", f"{util:.0f}%")

    if status.get("exhausted"):
        st.warning("Budget exhausted — no new analysis allowed.")

    if history:
        import pandas as pd

        df = pd.DataFrame(history)
        df = df.sort_values("month")
        st.bar_chart(df.set_index("month")["spent_usd"])
    else:
        st.info("No cost data available.")
