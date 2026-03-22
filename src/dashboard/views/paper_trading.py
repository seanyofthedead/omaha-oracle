"""Paper Trading page — Alpaca paper-trading integration hub.

Serves as the entry point for all paper-trading features.  Shows the
auth component and, once connected, a placeholder for sub-feature
worktrees (account overview, order entry, watchlists, options, analytics).
"""

from __future__ import annotations

import streamlit as st

from dashboard.alpaca_auth import render_alpaca_auth


def render() -> None:
    """Render the Paper Trading page."""
    st.title("Paper Trading")
    st.caption("Alpaca paper-trading — practice trades with virtual money, real market data.")

    client = render_alpaca_auth()

    if client is None:
        st.info(
            "Enter your Alpaca paper-trading API keys above to get started. "
            "Keys are stored only in your browser session and never saved to disk."
        )
        return

    st.divider()

    st.info(
        "Connected! Paper trading features (account overview, order entry, "
        "watchlists, options, analytics) will appear here as they are built."
    )
