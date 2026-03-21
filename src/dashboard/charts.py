"""Shared Plotly chart helpers for the Omaha Oracle dashboard.

Registers a custom ``omaha_oracle`` Plotly template that inherits from
``plotly_dark`` and applies the project's dark-transparent styling, font
stack, and brand colour-way.  The template is set as the global default so
every ``go.Figure()`` inherits it automatically.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# ── Brand palette (exported for explicit per-trace overrides) ────────────
ACCENT_BLUE = "#6C9EFF"
ACCENT_GREEN = "#4CAF50"
ACCENT_RED = "#F44336"
MUTED_GRAY = "rgba(255,255,255,0.25)"

# ── Build and register the template ─────────────────────────────────────
_base = pio.templates["plotly_dark"]

_omaha = go.layout.Template(_base)
_omaha.layout.update(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font={"family": "DM Sans, Inter, Segoe UI, system-ui, sans-serif", "size": 13},
    margin={"l": 48, "r": 24, "t": 48, "b": 40},
    hoverlabel={
        "bgcolor": "#1e1e1e",
        "font_size": 13,
        "font_color": "#e0e0e0",
    },
    colorway=[ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED],
)

pio.templates["omaha_oracle"] = _omaha
pio.templates.default = "omaha_oracle"
