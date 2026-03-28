"""Alpaca client singleton for the Streamlit dashboard.

Initializes once from ``.env`` / SSM via ``get_config().get_alpaca_keys()``.
Cached via ``@st.cache_resource`` so the ``TradingClient`` survives reruns.

Usage in any tab::

    from dashboard.alpaca_session import get_alpaca_client
    client = get_alpaca_client()
    positions = client.get_positions()
"""

from __future__ import annotations

import streamlit as st

from dashboard.alpaca_client import AlpacaClient
from shared.config import get_config


@st.cache_resource
def get_alpaca_client() -> AlpacaClient:
    """Return a cached AlpacaClient, initialized from env/SSM keys.

    Raises ``RuntimeError`` if keys are missing or the connection test fails.
    """
    api_key, secret_key = get_config().get_alpaca_keys()
    client = AlpacaClient(api_key, secret_key)
    ok, msg = client.test_connection()
    if not ok:
        raise RuntimeError(f"Alpaca connection failed: {msg}")
    return client


def validate_paper_trading() -> bool:
    """Check that the Alpaca base URL targets paper trading.

    Returns ``True`` if valid.  Shows ``st.error`` and returns ``False``
    if the URL does not contain ``paper``.
    """
    base_url = get_config().alpaca_base_url
    if "paper" not in base_url:
        st.error(
            "This dashboard is paper-trading only. "
            "ALPACA_BASE_URL must point to the paper trading API "
            "(https://paper-api.alpaca.markets)."
        )
        return False
    return True
