"""Portfolio Overview page."""

from __future__ import annotations

import streamlit as st

from dashboard.data import load_portfolio


def render() -> None:
    """Render the Portfolio Overview page."""
    st.title("Portfolio Overview")

    data = load_portfolio()
    cash = data.get("cash", 0)
    total = data.get("portfolio_value", 0)
    positions = data.get("positions", [])

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Portfolio Value", f"${total:,.0f}")
    with col2:
        st.metric("Cash", f"${cash:,.0f}")
    with col3:
        pct = (cash / total * 100) if total > 0 else 0
        st.metric("Cash %", f"{pct:.1f}%")

    if not positions:
        st.info("No positions on record.")
        return

    rows = []
    for p in positions:
        cost = p.get("cost_basis", 0) or 0
        mv = p.get("market_value", 0) or 0
        gain = mv - cost
        gain_pct = (gain / cost * 100) if cost > 0 else 0
        thesis = p.get("thesis_link") or ""
        rows.append(
            {
                "Ticker": p.get("ticker", ""),
                "Shares": p.get("shares", 0),
                "Cost Basis": f"${cost:,.0f}",
                "Market Value": f"${mv:,.0f}",
                "Gain/Loss": f"${gain:,.0f} ({gain_pct:+.1f}%)",
                "Sector": p.get("sector", ""),
                "Thesis": thesis if thesis else "—",
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)
