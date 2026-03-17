"""Signals page — recent buy/sell decisions with reasoning."""
from __future__ import annotations

import streamlit as st

from dashboard.data import load_decisions


def render() -> None:
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
        dtype = d.get("decision_type", "")
        payload = d.get("payload") or {}

        color = "green" if signal == "BUY" else "red" if signal == "SELL" else "gray"
        st.markdown(f"### :{color}[{signal}] {ticker} — {ts}")

        reasons = payload.get("reasons_pass") or payload.get("reasons_fail") or payload.get("reasons") or []
        if reasons:
            for r in reasons:
                st.markdown(f"- {r}")
        if payload.get("reasoning"):
            st.markdown(f"*{payload['reasoning']}*")

        st.divider()
