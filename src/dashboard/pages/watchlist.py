"""Watchlist page — companies under analysis."""
from __future__ import annotations

import streamlit as st

from dashboard.data import load_watchlist_analysis


def render() -> None:
    st.title("Watchlist")

    candidates = load_watchlist_analysis()
    if not candidates:
        st.info("No watchlist analysis available.")
        return

    rows = []
    for c in candidates:
        moat = c.get("moat_score") or 0
        mgmt = c.get("management_score") or 0
        iv = c.get("intrinsic_value_per_share") or c.get("intrinsic_value") or 0
        price = c.get("current_price") or 0
        mos = ((iv - price) / iv * 100) if iv > 0 else 0
        rows.append({
            "Ticker": c.get("ticker", ""),
            "Moat": moat,
            "Mgmt": mgmt,
            "IV": f"${iv:,.0f}" if iv else "—",
            "Price": f"${price:,.0f}" if price else "—",
            "MoS %": f"{mos:.0f}%" if iv else "—",
            "Sector": c.get("sector", ""),
        })

    st.dataframe(rows, use_container_width=True, hide_index=True)
