"""Formatting utilities for the Omaha Oracle dashboard.

Every user-visible number, date, or nullable value must pass through one of
these functions so the dashboard uses a single, consistent presentation style.

    Currency:     $1,234.56   (always $ prefix, comma separators)
    Short currency: $1.2M     (K / M / B abbreviation for hero metrics)
    Percentages:  12.3%       (always % suffix)
    Deltas:       +2.3% / -$450  (always sign prefix)
    Dates:        Mar 21, 2026   (never ISO or slash format)
    Nulls:        —            (em dash, never "None" / "NaN" / blank)
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

_Numeric = int | float | None

# ── Null / missing ──────────────────────────────────────────────────────

_PLACEHOLDER = "\u2014"  # em dash


def fmt_null(value: object, placeholder: str = _PLACEHOLDER) -> str:
    """Return *placeholder* for ``None``, ``NaN``, empty string, or 0.

    For truthy values the original string representation is returned.
    """
    if value is None:
        return placeholder
    if isinstance(value, float) and math.isnan(value):
        return placeholder
    if isinstance(value, str) and not value.strip():
        return placeholder
    return str(value)


# ── Currency ────────────────────────────────────────────────────────────


def fmt_currency(value: _Numeric, decimals: int = 0) -> str:
    """Format *value* as ``$1,234`` or ``$1,234.56``.

    Use in tables and detail views where precision matters.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    if value < 0:
        return f"-${abs(value):,.{decimals}f}"
    return f"${value:,.{decimals}f}"


def fmt_currency_short(value: _Numeric) -> str:
    """Format *value* with K / M / B suffix for hero metrics.

    Values under $10 000 are shown in full; larger values are abbreviated.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000_000:
        return f"{sign}${abs_v / 1_000_000_000:,.1f}B"
    if abs_v >= 1_000_000:
        return f"{sign}${abs_v / 1_000_000:,.1f}M"
    if abs_v >= 10_000:
        return f"{sign}${abs_v / 1_000:,.1f}K"
    return fmt_currency(value, decimals=0)


# ── Percentages ─────────────────────────────────────────────────────────


def fmt_pct(value: _Numeric, decimals: int = 1) -> str:
    """Format *value* as ``12.3%``.  *value* is in percentage points (not a 0-1 ratio)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    return f"{value:,.{decimals}f}%"


def fmt_pct_ratio(value: _Numeric, decimals: int = 0) -> str:
    """Format a 0-1 ratio as ``12%``.  Multiplies by 100 first."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    return f"{value * 100:,.{decimals}f}%"


# ── Deltas (always show sign) ──────────────────────────────────────────


def fmt_delta(value: _Numeric, decimals: int = 1) -> str:
    """Format *value* as ``+2.3%`` or ``-4.5%``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    return f"{value:+,.{decimals}f}%"


def fmt_delta_currency(value: _Numeric, decimals: int = 0) -> str:
    """Format *value* as ``+$1,234`` or ``-$450``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.{decimals}f}"


# ── Large numbers (non-currency) ──────────────────────────────────────


def fmt_large_number(value: _Numeric) -> str:
    """Format *value* as ``1.2K``, ``3.4M``, ``5.6B``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _PLACEHOLDER
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000_000:
        return f"{sign}{abs_v / 1_000_000_000:,.1f}B"
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:,.1f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:,.1f}K"
    return f"{sign}{abs_v:,.0f}"


# ── Dates ──────────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
]


def fmt_date(value: str | datetime | None) -> str:
    """Format a date as ``Mar 21, 2026``.

    Accepts ISO-8601 strings (``2026-03-21T14:30:00``) or ``datetime``
    objects.  Returns em-dash for ``None`` or unparseable input.
    """
    if value is None:
        return _PLACEHOLDER
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")
    if not isinstance(value, str) or not value.strip():
        return _PLACEHOLDER
    text = value.strip()
    # Strip timezone suffix if present (e.g. "+00:00", "Z")
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text[: text.rindex("+")]
    for pattern in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, pattern)
            return dt.strftime("%b %d, %Y")
        except ValueError:
            continue
    # Fallback: return as-is rather than crash
    return value


def fmt_datetime(value: str | datetime | None) -> str:
    """Format a datetime as ``Mar 21, 2026 2:30 PM``."""
    if value is None:
        return _PLACEHOLDER
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y %I:%M %p")
    if not isinstance(value, str) or not value.strip():
        return _PLACEHOLDER
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text[: text.rindex("+")]
    for pattern in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, pattern)
            if "T" in value and ":" in value:
                return dt.strftime("%b %d, %Y %I:%M %p")
            return dt.strftime("%b %d, %Y")
        except ValueError:
            continue
    return value


# ── Export ─────────────────────────────────────────────────────────────


def render_export_button(
    df: pd.DataFrame, filename_prefix: str, label: str = "Download CSV"
) -> None:
    """Render a Streamlit download button that exports *df* as CSV.

    The downloaded file is named ``omaha_oracle_{prefix}_{YYYY-MM-DD}.csv``.
    """
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    file_name = f"omaha_oracle_{filename_prefix}_{date_str}.csv"
    st.download_button(
        label=label,
        data=csv_bytes,
        file_name=file_name,
        mime="text/csv",
    )
