"""Shared Plotly chart helpers for the Omaha Oracle dashboard.

Provides a consistent dark theme and axis-formatting utilities so every
chart looks institutional-grade without repeating layout boilerplate.
"""

from __future__ import annotations

from typing import Any

_LAYOUT_DEFAULTS: dict[str, Any] = {
    "template": "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "DM Sans, Inter, Segoe UI, system-ui, sans-serif", "size": 13},
    "margin": {"l": 48, "r": 24, "t": 48, "b": 40},
    "hoverlabel": {
        "bgcolor": "#1e1e1e",
        "font_size": 13,
        "font_color": "#e0e0e0",
    },
}

ACCENT_BLUE = "#6C9EFF"
ACCENT_GREEN = "#4CAF50"
ACCENT_RED = "#F44336"
MUTED_GRAY = "rgba(255,255,255,0.25)"


def apply_theme(fig: Any) -> Any:
    """Apply the shared dark theme to a Plotly figure."""
    fig.update_layout(**_LAYOUT_DEFAULTS)
    return fig
