"""Shared Plotly chart helpers for the Omaha Oracle dashboard.

Registers a custom ``omaha_oracle`` Plotly template that inherits from
``plotly_dark`` and applies the project's dark-transparent styling, font
stack, and brand colour-way.  The template is set as the global default so
every ``go.Figure()`` inherits it automatically.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# ── Brand palette (exported for explicit per-trace overrides) ────────────
ACCENT_BLUE = "#6C9EFF"
ACCENT_GREEN = "#4CAF50"
ACCENT_RED = "#F44336"
MUTED_GRAY = "rgba(255,255,255,0.25)"

_FONT = {"family": "DM Sans, Inter, Segoe UI, system-ui, sans-serif", "size": 13}
_MARGIN = {"l": 48, "r": 24, "t": 48, "b": 40}
_COLORWAY = [ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED]

# ── Dark template ───────────────────────────────────────────────────────
_base_dark = pio.templates["plotly_dark"]

_omaha = go.layout.Template(_base_dark)
_omaha.layout.update(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=_FONT,
    margin=_MARGIN,
    hoverlabel={
        "bgcolor": "#1e1e1e",
        "font_size": 13,
        "font_color": "#e0e0e0",
    },
    colorway=_COLORWAY,
)

pio.templates["omaha_oracle"] = _omaha

# ── Light template ──────────────────────────────────────────────────────
_base_light = pio.templates["plotly_white"]

_omaha_light = go.layout.Template(_base_light)
_omaha_light.layout.update(
    paper_bgcolor="#FFFFFF",
    plot_bgcolor="#FFFFFF",
    font={**_FONT, "color": "#1A1A1A"},
    margin=_MARGIN,
    hoverlabel={
        "bgcolor": "#FFFFFF",
        "font_size": 13,
        "font_color": "#1A1A1A",
    },
    colorway=_COLORWAY,
    xaxis={"gridcolor": "#E0E0E0"},
    yaxis={"gridcolor": "#E0E0E0"},
)

pio.templates["omaha_oracle_light"] = _omaha_light

# ── Default to dark ─────────────────────────────────────────────────────
pio.templates.default = "omaha_oracle"


def get_chart_template() -> str:
    """Return the correct Plotly template name based on the theme toggle.

    Returns ``"omaha_oracle"`` when dark mode is active (the default) and
    ``"omaha_oracle_light"`` otherwise.
    """
    if st.session_state.get("dark_mode", True):
        return "omaha_oracle"
    return "omaha_oracle_light"
