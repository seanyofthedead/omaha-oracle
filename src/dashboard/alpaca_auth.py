"""Streamlit UI component for Alpaca paper-trading authentication.

Provides API key input, session-state management, connection status
indicator, and "Paper Trading" badge.  **Keys are never persisted to disk.**
"""

from __future__ import annotations

import streamlit as st

from dashboard.alpaca_client import AlpacaClient

# Session-state keys used by this module
_KEY_API = "alpaca_api_key"
_KEY_SECRET = "alpaca_secret_key"
_KEY_CLIENT = "alpaca_client"

# ── Session helpers (tested independently of Streamlit widgets) ───────


def store_alpaca_keys(api_key: str, secret_key: str) -> None:
    """Store non-empty API keys in session state."""
    if api_key and secret_key:
        st.session_state[_KEY_API] = api_key
        st.session_state[_KEY_SECRET] = secret_key


def is_alpaca_connected() -> bool:
    """Return True if an AlpacaClient is stored in session state."""
    return _KEY_CLIENT in st.session_state


def get_alpaca_client_from_session() -> AlpacaClient | None:
    """Return the session AlpacaClient or None."""
    return st.session_state.get(_KEY_CLIENT)


def clear_alpaca_session() -> None:
    """Remove all Alpaca keys and client from session state."""
    for key in (_KEY_API, _KEY_SECRET, _KEY_CLIENT):
        st.session_state.pop(key, None)


# ── Streamlit UI component ────────────────────────────────────────────


def render_alpaca_auth() -> AlpacaClient | None:
    """Render the API key form and connection status.

    Returns an ``AlpacaClient`` when connected, otherwise ``None``.
    """
    # Badge
    st.markdown(
        "<span style='background:#FF9800; color:#000; padding:3px 10px; "
        "border-radius:4px; font-weight:700; font-size:0.75rem;'>"
        "PAPER TRADING</span>",
        unsafe_allow_html=True,
    )

    if is_alpaca_connected():
        client = get_alpaca_client_from_session()
        st.success("Connected to Alpaca Paper Trading")
        if st.button("Disconnect", key="alpaca_disconnect"):
            clear_alpaca_session()
            st.rerun()
        return client

    with st.form("alpaca_auth_form"):
        api_key = st.text_input(
            "API Key ID",
            type="password",
            placeholder="APCA-API-KEY-ID",
        )
        secret_key = st.text_input(
            "Secret Key",
            type="password",
            placeholder="APCA-API-SECRET-KEY",
        )
        submitted = st.form_submit_button("Connect", width="stretch")

    if submitted:
        if not api_key or not secret_key:
            st.error("Both API Key and Secret Key are required.")
            return None

        store_alpaca_keys(api_key, secret_key)
        client = AlpacaClient(api_key, secret_key)
        ok, msg = client.test_connection()

        if ok:
            st.session_state[_KEY_CLIENT] = client
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)
            clear_alpaca_session()

    return None
