"""
Decision Journal — track falsifiable predictions on BUY theses.

Shows active predictions grouped by ticker with status, deadline,
metric, threshold, and actual value.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from dashboard.data import DataLoadError, load_predictions


def render() -> None:
    """Render the decision journal page."""
    st.header("Decision Journal")
    st.caption("Falsifiable predictions attached to BUY theses — auto-evaluated weekly")

    try:
        predictions = load_predictions()
    except DataLoadError as exc:
        st.error(str(exc))
        return

    if not predictions:
        st.info(
            "No predictions yet. Predictions are generated when the analysis "
            "pipeline produces a BUY thesis."
        )
        return

    # Summary metrics
    total = len(predictions)
    pending = sum(1 for p in predictions if p["status"] == "pending")
    confirmed = sum(1 for p in predictions if p["status"] == "CONFIRMED")
    falsified = sum(1 for p in predictions if p["status"] == "FALSIFIED")
    unresolvable = sum(1 for p in predictions if p["status"] == "UNRESOLVABLE")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Predictions", total)
    col2.metric("Pending", pending)
    col3.metric("Confirmed", confirmed)
    if total - pending - unresolvable > 0:
        rate = confirmed / (total - pending - unresolvable)
        col4.metric("Confirmation Rate", f"{rate:.0%}")
    else:
        col4.metric("Confirmation Rate", "—")

    # Filter
    status_options = ["All", "pending", "CONFIRMED", "FALSIFIED", "UNRESOLVABLE"]
    selected_status = st.selectbox("Filter by status", status_options, index=0)

    filtered = predictions
    if selected_status != "All":
        filtered = [p for p in predictions if p["status"] == selected_status]

    if not filtered:
        st.info(f"No predictions with status '{selected_status}'.")
        return

    # Group by ticker
    by_ticker: dict[str, list[dict]] = {}
    for p in filtered:
        by_ticker.setdefault(p["ticker"], []).append(p)

    for ticker in sorted(by_ticker.keys()):
        preds = by_ticker[ticker]
        with st.expander(f"{ticker} ({len(preds)} predictions)", expanded=True):
            for pred in preds:
                _render_prediction_card(pred)


def _status_badge(status: str) -> str:
    """Return a colored status indicator."""
    badges = {
        "pending": "🟡 Pending",
        "CONFIRMED": "🟢 Confirmed",
        "FALSIFIED": "🔴 Falsified",
        "UNRESOLVABLE": "⚪ Unresolvable",
    }
    return badges.get(status, status)


def _render_prediction_card(pred: dict) -> None:
    """Render a single prediction as a compact card."""
    status = pred.get("status", "pending")
    metric = pred.get("metric", "")
    operator = pred.get("operator", "")
    threshold = pred.get("threshold", "")
    deadline = pred.get("deadline", "")
    actual = pred.get("actual_value")
    description = pred.get("description", "")

    badge = _status_badge(status)

    cols = st.columns([3, 2, 2, 1])
    with cols[0]:
        st.markdown(f"**{badge}** — {description or f'{metric} {operator} {threshold}'}")
    with cols[1]:
        st.caption(f"Metric: `{metric}` {operator} {threshold}")
    with cols[2]:
        st.caption(f"Deadline: {deadline}")
    with cols[3]:
        if actual is not None:
            st.caption(f"Actual: {actual}")
        else:
            st.caption("Actual: —")
