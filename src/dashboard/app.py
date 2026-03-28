"""
Streamlit dashboard for Omaha Oracle — paper trading focused.

Pages: Account Summary, Portfolio, Trade History, Order Management,
       Performance Analytics, Watchlist & Pipeline.

All data comes from the Alpaca paper trading API, supplemented by
Oracle analysis data from DynamoDB/S3 where relevant.

Run locally:
  python run_dashboard.py
  # or: PYTHONPATH=src streamlit run src/dashboard/app.py
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
from streamlit_autorefresh import st_autorefresh

from dashboard.sidebar import render_sidebar
from dashboard.styles import apply_custom_styles

_PAGE_MODULES = {
    "Account Summary": "dashboard.views.account_summary",
    "Portfolio Overview": "dashboard.views.portfolio",
    "Trade History": "dashboard.views.trade_history",
    "Order Management": "dashboard.views.order_management",
    "Performance": "dashboard.views.performance",
    "Decision Journal": "dashboard.views.decision_journal",
    "Watchlist & Pipeline": "dashboard.views.pipeline",
}


def _show_tour() -> None:
    """Show a guided onboarding tour for first-time users."""
    step = st.session_state.get("tour_step", 0)

    tour_steps = [
        {
            "title": "Welcome to Omaha Oracle",
            "content": (
                "Omaha Oracle is an AI-powered value investing agent. This dashboard "
                "lets you monitor your **Alpaca paper trading** account, track positions, "
                "submit orders, and analyze performance — all in one place."
            ),
        },
        {
            "title": "Paper Trading Dashboard",
            "content": (
                "Every tab in this dashboard is connected to your Alpaca paper trading "
                "account:\n\n"
                "- **Account Summary** — Equity, cash, buying power, equity curve\n"
                "- **Portfolio Overview** — Open positions, P&L, sector allocation\n"
                "- **Trade History** — Completed trades with timeline\n"
                "- **Order Management** — Submit and manage orders\n"
                "- **Performance** — Returns vs SPY, drawdown, win rate\n"
                "- **Watchlist & Pipeline** — Oracle candidates and Alpaca watchlists"
            ),
        },
        {
            "title": "Risk Guardrails",
            "content": (
                "Hard-coded safety rules that cannot be overridden by AI:\n\n"
                "- Max **15%** of portfolio in any single position\n"
                "- Max **35%** sector exposure\n"
                "- Min **10%** cash reserve at all times\n"
                "- **Zero** leverage, shorts, options, or crypto"
            ),
        },
        {
            "title": "You're Ready!",
            "content": (
                "Use the sidebar to navigate between pages. Your API keys are loaded "
                "automatically from your `.env` file — no need to enter them.\n\n"
                "You can restart this tour anytime from the sidebar."
            ),
        },
    ]

    if step >= len(tour_steps):
        st.session_state.tour_completed = True
        return

    current = tour_steps[step]

    with st.container(border=True):
        col_title, col_counter = st.columns([4, 1])
        with col_title:
            st.subheader(current["title"])
        with col_counter:
            st.caption(f"Step {step + 1} of {len(tour_steps)}")

        st.markdown(current["content"])

        col_skip, col_spacer, col_next = st.columns([1, 3, 1])
        with col_skip:
            if st.button("Skip tour", key="tour_skip"):
                st.session_state.tour_dismissed = True
                st.rerun()
        with col_next:
            label = "Get Started" if step == len(tour_steps) - 1 else "Next"
            if st.button(label, key="tour_next", type="primary"):
                st.session_state.tour_step = step + 1
                if step + 1 >= len(tour_steps):
                    st.session_state.tour_completed = True
                st.rerun()


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
            st.title("Omaha Oracle")
            st.caption("Paper Trading Dashboard")
            with st.form("login_form"):
                pwd = st.text_input(
                    "Password",
                    type="password",
                    key="login_pwd",
                    placeholder="Enter dashboard password",
                )
                submitted = st.form_submit_button("Login", width="stretch")
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


def _require_alpaca_keys() -> None:
    """Validate Alpaca API keys and paper trading base URL at startup."""
    from dashboard.alpaca_session import validate_paper_trading
    from shared.config import get_config

    # Check base URL is paper trading
    if not validate_paper_trading():
        st.stop()

    # Check keys are available
    try:
        get_config().get_alpaca_keys()
    except RuntimeError:
        st.error(
            "Alpaca API keys not configured. Add ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY to your `.env` file, or store them in "
            "AWS SSM Parameter Store."
        )
        st.code(
            "# .env\n"
            "ALPACA_API_KEY=your-key-here\n"
            "ALPACA_SECRET_KEY=your-secret-here\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets",
            language="bash",
        )
        st.stop()


def main() -> None:
    """Configure the Streamlit app and render the selected page."""
    st.set_page_config(
        page_title="Omaha Oracle | Paper Trading",
        page_icon="♠",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_custom_styles()

    # Auto-refresh if enabled
    if st.session_state.get("auto_refresh", False):
        interval_min = st.session_state.get("refresh_interval", 5)
        st_autorefresh(interval=interval_min * 60 * 1000, key="auto_refresh_timer")

    _require_auth()
    _require_alpaca_keys()

    # ── Onboarding Tour ──
    if not st.session_state.get("tour_completed", False) and not st.session_state.get(
        "tour_dismissed", False
    ):
        _show_tour()

    selection = render_sidebar(list(_PAGE_MODULES.keys()))

    page = importlib.import_module(_PAGE_MODULES[selection])
    page.render()


if __name__ == "__main__":
    main()
