"""Cost Tracker page — monthly spend and budget."""

from __future__ import annotations

import streamlit as st

from dashboard.data import DataLoadError, load_cost_data
from dashboard.fmt import fmt_currency, fmt_pct


def render() -> None:
    """Render the Cost Tracker page."""
    st.title("Cost Tracker")
    st.caption(
        "LLM API spend tracking and monthly budget status — "
        "analysis pauses automatically when the budget is "
        "exhausted."
    )

    # Filters in sidebar
    months = st.sidebar.slider(
        "Months to show",
        3,
        24,
        12,
        help="How many months of spend history to display in the trend chart and breakdown table.",
    )

    try:
        with st.spinner("Loading LLM spend history and budget status..."):
            history, status = load_cost_data(months=months)
    except DataLoadError as exc:
        st.error(str(exc))
        return

    budget = status.get("budget_usd", 0)
    spent = status.get("spent_usd", 0)
    remaining = status.get("remaining_usd", 0)
    util = status.get("utilization_pct", 0)

    # ── Tier 1: Hero metrics ──
    col1, col2, col3, col4 = st.columns(4, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric(
            "Monthly Budget",
            fmt_currency(budget),
            help="Maximum allowed AI API spend per month, "
            "set via MONTHLY_LLM_BUDGET_CENTS in config.",
        )
    with col2:
        st.metric(
            "Spent",
            fmt_currency(spent, decimals=2),
            help="LLM spend so far this month.",
        )
    with col3:
        st.metric(
            "Remaining",
            fmt_currency(remaining, decimals=2),
            help="Budget remaining for this month.",
        )
    with col4:
        st.metric(
            "Utilization",
            fmt_pct(util, 0),
            delta=("EXHAUSTED" if status.get("exhausted") else None),
            delta_color=("inverse" if status.get("exhausted") else "off"),
            help="Percentage of monthly budget consumed. Warning at 80%, full stop at 100%.",
        )

    if status.get("exhausted"):
        st.error(
            "Budget exhausted — all analysis is paused until "
            "next month. Increase MONTHLY_LLM_BUDGET_CENTS in "
            "config to resume."
        )
    elif util >= 80:
        st.warning(
            f"Budget is {fmt_pct(util, 0)} consumed. "
            f"Only {fmt_currency(remaining, 2)} remaining this "
            "month. Consider deferring non-critical analysis runs."
        )

    if "page_toast_shown" not in st.session_state:
        st.toast(
            f"Showing {months} months of spend data",
            icon="✅",
        )
        st.session_state.page_toast_shown = True

    st.divider()

    # ── Tier 2: Primary content in tabs ──
    tab_trend, tab_breakdown = st.tabs(["Spend Trend", "Monthly Breakdown"])

    with tab_trend:
        if history:
            import pandas as pd
            import plotly.graph_objects as go

            from dashboard.charts import (
                ACCENT_BLUE,
                MUTED_GRAY,
            )

            df = pd.DataFrame(history)
            df = df.sort_values("month")
            with st.container(border=True):
                fig = go.Figure(
                    go.Bar(
                        x=df["month"],
                        y=df["spent_usd"],
                        marker_color=ACCENT_BLUE,
                        hovertemplate=("<b>%{x}</b><br>Spent: $%{y:,.2f}<extra></extra>"),
                    )
                )
                if budget > 0:
                    fig.add_hline(
                        y=budget,
                        line_dash="dash",
                        line_color=MUTED_GRAY,
                        annotation_text="Budget",
                        annotation_position="top left",
                        annotation_font_color=MUTED_GRAY,
                    )
                fig.update_layout(
                    title="Monthly LLM Spend vs Budget",
                    xaxis_title=None,
                    yaxis_title=None,
                    yaxis_tickprefix="$",
                    yaxis_tickformat=",.0f",
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(
                    "Dashed line shows the monthly budget. "
                    "Bars exceeding it indicate months where "
                    "spend was manually increased or a "
                    "config change took effect mid-month."
                )
        else:
            st.info(
                "No spend data recorded yet. Data appears after "
                "the first analysis pipeline run incurs LLM costs."
            )

    with tab_breakdown:
        if history:
            import pandas as pd

            df = pd.DataFrame(history)
            df = df.sort_values("month", ascending=False)
            with st.container(border=True):
                st.dataframe(
                    df.rename(
                        columns={
                            "month": "Month",
                            "spent_usd": "Spent",
                        }
                    ),
                    column_config={
                        "Month": st.column_config.TextColumn("Month"),
                        "Spent": st.column_config.NumberColumn("Spent", format="$%,.2f"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    height=min(len(history) * 35 + 38, 400),
                )
        else:
            st.info(
                "No spend data recorded yet. Data appears after "
                "the first analysis pipeline run incurs LLM costs."
            )

    # ── Tier 3: Supplementary content ──
    st.divider()
    with st.expander("Budget Configuration"):
        st.json(status)
