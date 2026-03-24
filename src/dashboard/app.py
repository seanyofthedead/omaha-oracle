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
}


def _show_tour() -> None:
    """Show a guided onboarding tour for first-time users."""
    step = st.session_state.get("tour_step", 0)

    tour_steps = [
        {
            "title": "Welcome to Omaha Oracle",
            "content": (
                "Omaha Oracle is an autonomous AI-powered stock-picking agent built on "
                "Graham-Dodd-Buffett value investing principles. This dashboard lets you "
                "monitor its decisions, analyze candidates, and track portfolio performance."
            ),
        },
        {
            "title": "The 5-Stage Analysis Pipeline",
            "content": (
                "Every stock candidate goes through 5 stages:\n\n"
                "1. **Quantitative Screen** — Pure math filters (P/E, P/B, ROIC, Piotroski F-Score)\n"
                "2. **Moat Analysis** — AI evaluates competitive advantages\n"
                "3. **Management Quality** — AI scores owner-operator mindset and capital allocation\n"
                "4. **Intrinsic Value** — DCF, EPV, and asset floor calculations\n"
                "5. **Investment Thesis** — AI writes a full Buffett-style thesis"
            ),
        },
        {
            "title": "Self-Improvement Loop",
            "content": (
                "Every quarter, the system runs a post-mortem audit:\n\n"
                "- Classifies past decisions as good/bad based on actual outcomes\n"
                "- Extracts lessons from mistakes\n"
                "- Injects those lessons into future AI analysis prompts\n"
                "- Auto-adjusts screening thresholds\n\n"
                "Visit the **Feedback Loop** page to see active lessons and mistake rate trends."
            ),
        },
        {
            "title": "Risk Guardrails",
            "content": (
                "Hard-coded safety rules that cannot be overridden by AI:\n\n"
                "- Max **15%** of portfolio in any single position\n"
                "- Max **35%** sector exposure\n"
                "- Min **10%** cash reserve at all times\n"
                "- **Zero** leverage, shorts, options, or crypto\n\n"
                "Visit the **Portfolio Overview** page to see guardrail compliance."
            ),
        },
        {
            "title": "You're Ready!",
            "content": (
                "Use the sidebar to navigate between pages. Key pages:\n\n"
                "- **Portfolio Overview** — Current holdings and health\n"
                "- **Watchlist** — Candidates under evaluation\n"
                "- **Signals** — Recent BUY/SELL decisions\n"
                "- **Company Search** — Find new candidates\n"
                "- **Backtest** — Replay past performance\n\n"
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
