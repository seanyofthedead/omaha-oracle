"""Options order building and validation logic.

Pure functions for constructing single-leg and multi-leg option order
parameters. No Streamlit or API calls.
"""

from __future__ import annotations


def build_vertical_spread_legs(
    long_symbol: str,
    short_symbol: str,
) -> list[dict[str, str | int]]:
    """Build legs for a vertical spread (bull call or bear put)."""
    return [
        {"symbol": long_symbol, "ratio_qty": 1, "side": "buy"},
        {"symbol": short_symbol, "ratio_qty": 1, "side": "sell"},
    ]


def build_straddle_legs(
    call_symbol: str,
    put_symbol: str,
) -> list[dict[str, str | int]]:
    """Build legs for a long straddle (buy call + buy put at same strike)."""
    return [
        {"symbol": call_symbol, "ratio_qty": 1, "side": "buy"},
        {"symbol": put_symbol, "ratio_qty": 1, "side": "buy"},
    ]


def build_strangle_legs(
    call_symbol: str,
    put_symbol: str,
) -> list[dict[str, str | int]]:
    """Build legs for a long strangle (buy OTM call + buy OTM put)."""
    return [
        {"symbol": call_symbol, "ratio_qty": 1, "side": "buy"},
        {"symbol": put_symbol, "ratio_qty": 1, "side": "buy"},
    ]


def validate_option_order(
    symbol: str,
    qty: int | float,
    side: str,
    order_type: str,
    limit_price: float | None = None,
) -> list[str]:
    """Validate option order parameters. Returns a list of error messages (empty = valid)."""
    errors: list[str] = []
    if not symbol:
        errors.append("Option symbol is required.")
    if qty <= 0:
        errors.append("Quantity must be greater than zero.")
    if side not in ("buy", "sell"):
        errors.append(f"Invalid side: {side}. Must be 'buy' or 'sell'.")
    if order_type == "limit" and not limit_price:
        errors.append("Limit price is required for limit orders.")
    return errors
