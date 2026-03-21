"""
Streamlit dashboard for Omaha Oracle — monitoring tool.

Pages: Portfolio, Watchlist, Signals, Cost Tracker, Owner's Letters, Feedback Loop.
Uses boto3 to read from DynamoDB and S3.

Run locally:
  python run_dashboard.py
  # or: PYTHONPATH=src streamlit run src/dashboard/app.py

For Lambda deployment, use a container (Streamlit is not ASGI/Mangum-compatible).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on path when run as streamlit run dashboard/app.py
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib

import streamlit as st

_PAGE_MODULES = {
    "Portfolio Overview": "dashboard.views.portfolio",
    "Watchlist": "dashboard.views.watchlist",
    "Signals": "dashboard.views.signals",
    "Cost Tracker": "dashboard.views.cost_tracker",
    "Owner's Letters": "dashboard.views.letters",
    "Feedback Loop": "dashboard.views.feedback_loop",
}


def _require_auth() -> None:
    """
    Block access until the user provides the correct dashboard password.

    Password is stored in .streamlit/secrets.toml:
        dashboard_password = "your-password-here"

    Session persists until the browser tab is closed or Streamlit restarts.
    """
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.set_page_config(page_title="Omaha Oracle — Login", page_icon="🔒")
        st.title("🔒 Omaha Oracle")
        st.markdown("Enter the dashboard password to continue.")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Login"):
            expected = st.secrets.get("dashboard_password", "")
            if expected and pwd == expected:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()


def main() -> None:
    """Configure the Streamlit app and render the selected page."""
    _require_auth()
    st.set_page_config(
        page_title="Omaha Oracle",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.sidebar.title("Omaha Oracle")
    st.sidebar.markdown("*Monitoring dashboard*")

    selection = st.sidebar.radio("Page", list(_PAGE_MODULES.keys()))
    page = importlib.import_module(_PAGE_MODULES[selection])
    page.render()


if __name__ == "__main__":
    main()
