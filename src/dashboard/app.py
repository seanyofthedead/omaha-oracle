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

import streamlit as st

from dashboard.pages import (
    cost_tracker,
    feedback_loop,
    letters,
    portfolio,
    signals,
    watchlist,
)

PAGES = {
    "Portfolio Overview": portfolio,
    "Watchlist": watchlist,
    "Signals": signals,
    "Cost Tracker": cost_tracker,
    "Owner's Letters": letters,
    "Feedback Loop": feedback_loop,
}


def main() -> None:
    st.set_page_config(
        page_title="Omaha Oracle",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.sidebar.title("Omaha Oracle")
    st.sidebar.markdown("*Monitoring dashboard*")

    selection = st.sidebar.radio("Page", list(PAGES.keys()))
    page = PAGES[selection]
    page.render()


if __name__ == "__main__":
    main()
