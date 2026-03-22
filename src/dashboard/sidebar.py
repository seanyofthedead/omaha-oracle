"""Shared sidebar for the Omaha Oracle dashboard.

Call ``render_sidebar()`` once from ``app.py`` after ``set_page_config()``.
It renders the branded sidebar (logo, navigation, footer) and returns the
name of the selected page so the caller can dispatch to the right module.

Page-specific filters (sliders, selectboxes) remain in individual view
modules — Streamlit appends them to the sidebar in execution order.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

_APP_VERSION = "0.1.0"
_ASSETS = Path(__file__).resolve().parent / "assets"

_PAGE_ICONS = {
    "Portfolio Overview": ":material/account_balance:",
    "Watchlist": ":material/visibility:",
    "Signals": ":material/trending_up:",
    "Cost Tracker": ":material/payments:",
    "Owner's Letters": ":material/mail:",
    "Feedback Loop": ":material/autorenew:",
    "Upload Analysis": ":material/upload_file:",
    "Company Search": ":material/search:",
}


def _get_environment() -> str:
    """Read the current environment, falling back to 'dev'."""
    try:
        from shared.config import get_config

        return get_config().environment
    except Exception:
        import os

        return os.getenv("ENVIRONMENT", "dev")


def render_sidebar(page_names: list[str]) -> str:
    """Render the branded sidebar and return the selected page name.

    Parameters
    ----------
    page_names:
        Ordered list of page display names (keys of ``_PAGE_MODULES``).

    Returns
    -------
    str
        The page name selected by the user.
    """
    # ── Logo (persistent, pinned to top of sidebar) ──
    st.logo(
        str(_ASSETS / "logo.svg"),
        icon_image=str(_ASSETS / "logo_small.svg"),
    )

    # ── Branding ──
    st.sidebar.markdown(
        "<p style='margin:0 0 -6px 0; font-size:0.85rem; "
        "color:#888; font-style:italic;'>"
        "Portfolio Intelligence Dashboard</p>",
        unsafe_allow_html=True,
    )
    st.sidebar.divider()

    # ── Navigation ──
    selection = st.sidebar.radio(
        "Navigate",
        page_names,
        format_func=lambda name: f"{_PAGE_ICONS.get(name, '')}  {name}",
        label_visibility="collapsed",
    )

    st.sidebar.divider()

    # ── Refresh Data ──
    if st.sidebar.button("↻ Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ── Footer ──
    env = _get_environment()
    env_color = "#4CAF50" if env == "dev" else "#F44336"
    env_label = env.upper()
    refreshed = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    st.sidebar.markdown(
        f"<div style='font-size:0.78rem; color:#888; line-height:1.7;'>"
        f"<span style='background:{env_color}; color:#fff; "
        f"padding:2px 8px; border-radius:4px; font-weight:600; "
        f"font-size:0.72rem;'>{env_label}</span>"
        f"&nbsp; v{_APP_VERSION}<br>"
        f"Refreshed {refreshed}"
        f"</div>",
        unsafe_allow_html=True,
    )

    return selection
