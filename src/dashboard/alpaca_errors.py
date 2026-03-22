"""Unified error handling for Alpaca API calls.

Translates alpaca-py ``APIError`` exceptions into user-friendly Streamlit
messages with specific handling for common failure modes.
"""

from __future__ import annotations

import streamlit as st
from alpaca.common.exceptions import APIError

# ── Typed dashboard errors ────────────────────────────────────────────────


class AlpacaDashboardError:
    """Base wrapper carrying a user-facing message."""

    def __init__(self, user_message: str, original: Exception | None = None) -> None:
        self.user_message = user_message
        self.original = original


class AlpacaAuthError(AlpacaDashboardError):
    pass


class AlpacaRateLimitError(AlpacaDashboardError):
    pass


class AlpacaInvalidSymbolError(AlpacaDashboardError):
    pass


class AlpacaInsufficientFundsError(AlpacaDashboardError):
    pass


class AlpacaPDTViolationError(AlpacaDashboardError):
    pass


class AlpacaMarketClosedError(AlpacaDashboardError):
    pass


# ── Classification logic ──────────────────────────────────────────────────

_SYMBOL_MSG = "Symbol not found. Check the ticker and try again."

_KEYWORD_MAP: list[tuple[str, type[AlpacaDashboardError], str]] = [
    (
        "insufficient buying power",
        AlpacaInsufficientFundsError,
        "Insufficient buying power to complete this order.",
    ),
    (
        "pattern day trad",
        AlpacaPDTViolationError,
        "Pattern Day Trader restriction — your account has made "
        "3+ day trades in 5 business days with equity under $25K.",
    ),
    (
        "market is not open",
        AlpacaMarketClosedError,
        "The market is currently closed. Try again during "
        "market hours (9:30 AM \u2013 4:00 PM ET).",
    ),
    ("asset not found", AlpacaInvalidSymbolError, _SYMBOL_MSG),
    ("could not find asset", AlpacaInvalidSymbolError, _SYMBOL_MSG),
]


def classify_alpaca_error(exc: Exception) -> AlpacaDashboardError:
    """Map *exc* to the most specific ``AlpacaDashboardError`` subclass."""
    if isinstance(exc, APIError):
        status = exc.status_code
        try:
            msg_lower = exc.message.lower()
        except Exception:
            msg_lower = str(exc).lower()

        # Check keyword matches first (more specific than status code)
        for keyword, cls, user_msg in _KEYWORD_MAP:
            if keyword in msg_lower:
                return cls(user_msg, exc)

        # Status-code fallbacks
        if status in (401, 403):
            return AlpacaAuthError(
                "Authentication failed. Check your API keys and try reconnecting.",
                exc,
            )
        if status == 429:
            return AlpacaRateLimitError(
                "Alpaca rate limit exceeded. Please wait a moment and try again.",
                exc,
            )
        if status == 404:
            return AlpacaInvalidSymbolError(
                "Symbol not found. Check the ticker and try again.",
                exc,
            )
        if status == 422:
            return AlpacaInvalidSymbolError(
                "Symbol not found. Check the ticker and try again.",
                exc,
            )

        return AlpacaDashboardError(
            f"Alpaca API error (HTTP {status}). Please try again shortly.",
            exc,
        )

    # Non-API errors
    from requests.exceptions import Timeout

    if isinstance(exc, Timeout):
        return AlpacaDashboardError(
            "Request timed out. Check your connection and try again.",
            exc,
        )
    if isinstance(exc, ConnectionError):
        return AlpacaDashboardError(
            "Connection error. Check your network and try again.",
            exc,
        )

    return AlpacaDashboardError(
        f"Unexpected error: {type(exc).__name__}. Please try again.",
        exc,
    )


# ── Streamlit display helper ──────────────────────────────────────────────


def handle_alpaca_error(exc: Exception) -> AlpacaDashboardError:
    """Classify *exc* and display the appropriate ``st.error`` or ``st.warning``.

    Returns the classified error so callers can branch on type if needed.
    """
    classified = classify_alpaca_error(exc)

    # Rate-limit and market-closed are transient — use warning
    if isinstance(classified, (AlpacaRateLimitError, AlpacaMarketClosedError)):
        st.warning(classified.user_message)
    else:
        st.error(classified.user_message)

    return classified
