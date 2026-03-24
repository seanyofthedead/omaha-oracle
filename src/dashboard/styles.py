"""Shared CSS design system for the Omaha Oracle dashboard."""

from __future__ import annotations

import plotly.io as pio
import streamlit as st

_LIGHT_CSS = """
<style>
/* ── AREA 1: SPACING & LAYOUT ────────────────────────────────────── */

/* Reduce top padding from ~6rem to ~2rem */
[data-testid="stAppViewContainer"] > .main {
    padding-top: 2rem;
}

/* Tighten block-container for denser display */
[data-testid="stAppViewBlockContainer"] {
    max-width: 1400px;
    padding-left: 2rem;
    padding-right: 2rem;
}

/* ── AREA 2: METRIC CARDS ────────────────────────────────────────── */

[data-testid="stMetric"] {
    background: #F0F0F0;
    border: 1px solid #E0E0E0;
    border-radius: 12px;
    padding: 16px;
}

/* Slightly larger delta text */
[data-testid="stMetricDelta"] {
    font-size: 0.95rem;
}

/* ── AREA 3: SIDEBAR ─────────────────────────────────────────────── */

[data-testid="stSidebar"] {
    padding-top: 1.5rem;
    background: #F5F5F5;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"]
> [data-testid="stVerticalBlockBorderWrapper"] {
    border-bottom: 1px solid #E0E0E0;
    padding-bottom: 0.75rem;
    margin-bottom: 0.75rem;
}

/* ── AREA 4: DATAFRAMES ──────────────────────────────────────────── */

/* Alternating row colors */
[data-testid="stDataFrame"] table tbody tr:nth-child(even) {
    background: #F8F8F8;
}

[data-testid="stDataFrame"] table tbody tr:nth-child(odd) {
    background: #FFFFFF;
}

/* Bold column headers with subtle tint */
[data-testid="stDataFrame"] table thead th {
    font-weight: 700 !important;
    background: rgba(108, 158, 255, 0.1) !important;
}

/* ── AREA 5: TABS ────────────────────────────────────────────────── */

/* Remove heavy default underline from tab bar */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid #E0E0E0;
}

/* Individual tab styling */
.stTabs [data-baseweb="tab"] {
    padding: 8px 20px;
    border-bottom: 2px solid transparent;
}

/* Active tab — clean 2px accent border */
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 2px solid #6C9EFF;
}

/* Remove default tab highlight bar */
.stTabs [data-baseweb="tab-highlight"] {
    display: none;
}

/* ── AREA 6: GENERAL POLISH ──────────────────────────────────────── */

/* Main app background */
[data-testid="stAppViewContainer"] {
    background: #FFFFFF;
    color: #1A1A1A;
}

/* Hide hamburger menu, footer, and header decoration */
#MainMenu {
    visibility: hidden;
}

[data-testid="stHeader"] {
    background: transparent;
}

footer {
    visibility: hidden;
}

/* Smooth hover transitions on all buttons */
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"],
button[kind="secondary"],
button[kind="primary"] {
    transition: background-color 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}

[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stBaseButton-primary"]:hover,
button[kind="secondary"]:hover,
button[kind="primary"]:hover {
    transform: translateY(-1px);
}

/* Expander headers — pointer cursor and hover background */
[data-testid="stExpander"] summary {
    cursor: pointer;
    border-radius: 8px;
    padding: 4px 8px;
    transition: background-color 0.2s ease;
}

[data-testid="stExpander"] summary:hover {
    background: rgba(0, 0, 0, 0.05);
}
</style>
"""

_DARK_CSS = """
<style>
/* ── AREA 1: SPACING & LAYOUT ────────────────────────────────────── */

/* Reduce top padding from ~6rem to ~2rem */
[data-testid="stAppViewContainer"] > .main {
    padding-top: 2rem;
}

/* Tighten block-container for denser display */
[data-testid="stAppViewBlockContainer"] {
    max-width: 1400px;
    padding-left: 2rem;
    padding-right: 2rem;
}

/* ── AREA 2: METRIC CARDS ────────────────────────────────────────── */

[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 16px;
}

/* Slightly larger delta text */
[data-testid="stMetricDelta"] {
    font-size: 0.95rem;
}

/* ── AREA 3: SIDEBAR ─────────────────────────────────────────────── */

[data-testid="stSidebar"] {
    padding-top: 1.5rem;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding-bottom: 0.75rem;
    margin-bottom: 0.75rem;
}

/* ── AREA 4: DATAFRAMES ──────────────────────────────────────────── */

/* Alternating row colors */
[data-testid="stDataFrame"] table tbody tr:nth-child(even) {
    background: rgba(255, 255, 255, 0.03);
}

/* Bold column headers with subtle tint */
[data-testid="stDataFrame"] table thead th {
    font-weight: 700 !important;
    background: rgba(108, 158, 255, 0.08) !important;
}

/* ── AREA 5: TABS ────────────────────────────────────────────────── */

/* Remove heavy default underline from tab bar */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

/* Individual tab styling */
.stTabs [data-baseweb="tab"] {
    padding: 8px 20px;
    border-bottom: 2px solid transparent;
}

/* Active tab — clean 2px accent border */
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 2px solid #6C9EFF;
}

/* Remove default tab highlight bar */
.stTabs [data-baseweb="tab-highlight"] {
    display: none;
}

/* ── AREA 6: GENERAL POLISH ──────────────────────────────────────── */

/* Hide hamburger menu, footer, and header decoration */
#MainMenu {
    visibility: hidden;
}

[data-testid="stHeader"] {
    background: transparent;
}

footer {
    visibility: hidden;
}

/* Smooth hover transitions on all buttons */
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"],
button[kind="secondary"],
button[kind="primary"] {
    transition: background-color 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}

[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stBaseButton-primary"]:hover,
button[kind="secondary"]:hover,
button[kind="primary"]:hover {
    transform: translateY(-1px);
}

/* Expander headers — pointer cursor and hover background */
[data-testid="stExpander"] summary {
    cursor: pointer;
    border-radius: 8px;
    padding: 4px 8px;
    transition: background-color 0.2s ease;
}

[data-testid="stExpander"] summary:hover {
    background: rgba(255, 255, 255, 0.05);
}
</style>
"""


def apply_custom_styles(theme: str = "dark") -> None:
    """Inject the shared CSS design system into the current page.

    Call immediately after ``st.set_page_config()``.

    Parameters
    ----------
    theme:
        ``"dark"`` or ``"light"``.  When omitted the function checks
        ``st.session_state`` for the ``dark_mode`` toggle and falls
        back to dark.
    """
    is_dark = st.session_state.get("dark_mode", True)
    if is_dark:
        st.markdown(_DARK_CSS, unsafe_allow_html=True)
        pio.templates.default = "omaha_oracle"
    else:
        st.markdown(_LIGHT_CSS, unsafe_allow_html=True)
        pio.templates.default = "omaha_oracle_light"
