"""Paper Trading page — Alpaca paper-trading integration hub.

Serves as the entry point for all paper-trading features.  Shows the
auth component and, once connected, the account overview, portfolio
positions, and order entry.  Additional sub-features (watchlists,
options, analytics) will be added by other worktrees.
"""

from __future__ import annotations

import streamlit as st

from dashboard.alpaca_auth import render_alpaca_auth
from dashboard.views import order_entry
from dashboard.views.account_portfolio import (
    render_account_overview,
    render_positions_table,
)


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

    render_account_overview(client)

    st.divider()

    render_positions_table(client)

    st.divider()

    order_entry.render(client)
