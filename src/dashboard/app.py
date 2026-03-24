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

import hmac
import importlib
import sys
import time
from pathlib import Path

# Ensure src is on path when run as streamlit run dashboard/app.py
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from dashboard.sidebar import render_sidebar
from dashboard.styles import apply_custom_styles

_PAGE_MODULES = {
    "Portfolio Overview": "dashboard.views.portfolio",
    "Watchlist": "dashboard.views.watchlist",
    "Signals": "dashboard.views.signals",
    "Cost Tracker": "dashboard.views.cost_tracker",
    "Owner's Letters": "dashboard.views.letters",
    "Feedback Loop": "dashboard.views.feedback_loop",
    "Upload Analysis": "dashboard.views.upload_analysis",
    "Company Search": "dashboard.views.company_search",
    "Paper Trading": "dashboard.views.paper_trading",
    "Sector Insights": "dashboard.views.sectors",
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
        _, center, _ = st.columns([1, 2, 1])
        with center:
            st.title("♠ Omaha Oracle")
            st.caption("Portfolio Intelligence Dashboard")
            with st.form("login_form"):
                pwd = st.text_input(
                    "Password",
                    type="password",
                    key="login_pwd",
                    placeholder="Enter dashboard password",
                )
                submitted = st.form_submit_button("Login", use_container_width=True)
            if submitted:
                # Rate limiting: track failed attempts within a 60-second window
                if "login_attempts" not in st.session_state:
                    st.session_state.login_attempts = []
                now = time.time()
                # Prune attempts older than 60 seconds
                st.session_state.login_attempts = [
                    t for t in st.session_state.login_attempts if now - t < 60
                ]
                if len(st.session_state.login_attempts) >= 5:
                    st.error(
                        "Too many failed attempts. Please wait 60 seconds before trying again."
                    )
                else:
                    expected = st.secrets.get("dashboard_password", "")
                    if expected and hmac.compare_digest(pwd.encode(), expected.encode()):
                        st.session_state.authenticated = True
                        st.session_state.login_attempts = []
                        st.rerun()
                    else:
                        st.session_state.login_attempts.append(now)
                        st.error("Incorrect password.")
        st.stop()


def main() -> None:
    """Configure the Streamlit app and render the selected page."""
    st.set_page_config(
        page_title="Omaha Oracle | Dashboard",
        page_icon="♠",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_custom_styles()
    _require_auth()

    selection = render_sidebar(list(_PAGE_MODULES.keys()))

    page = importlib.import_module(_PAGE_MODULES[selection])
    page.render()


if __name__ == "__main__":
    main()
