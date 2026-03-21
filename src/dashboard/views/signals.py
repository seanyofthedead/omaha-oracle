"""Signals page — recent buy/sell decisions with reasoning."""

from __future__ import annotations

import streamlit as st

from dashboard.data import load_decisions


def render() -> None:
    """Render the Signals page."""
    st.title("Signals")

    limit = st.sidebar.slider("Max signals", 10, 100, 30)
    decisions = load_decisions(limit=limit)

    if not decisions:
        st.info("No decisions on record.")
        return

    for d in decisions:
        signal = (d.get("signal") or "").upper()
        ticker = d.get("ticker", "")
        ts = d.get("timestamp", "")[:19]
        payload = d.get("payload") or {}

        color = "green" if signal == "BUY" else "red" if signal == "SELL" else "gray"
        st.subheader(f":{color}[{signal}]  {ticker}  —  {ts}")

        reasons = (
            payload.get("reasons_pass")
            or payload.get("reasons_fail")
            or payload.get("reasons")
            or []
        )
        if reasons:
            for r in reasons:
                st.write(f"• {r}")
        if payload.get("reasoning"):
            st.caption(payload["reasoning"])

        st.divider()
