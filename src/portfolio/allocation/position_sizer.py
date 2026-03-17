"""
Position sizing via Half-Kelly criterion.

f* = (bp - q) / 2b
  p = win probability, q = 1-p, b = win/loss ratio
"""
from __future__ import annotations

from typing import Any


def calculate_position_size(
    win_probability: float,
    win_loss_ratio: float,
    portfolio_value: float,
    current_positions: list[dict[str, Any]],
    max_position_pct: float = 0.15,
    min_position_usd: float = 2000.0,
    max_positions: int = 20,
) -> dict[str, Any]:
    """
    Compute position size using Half-Kelly criterion.

    Parameters
    ----------
    win_probability : float
        Probability of a winning outcome (0–1).
    win_loss_ratio : float
        Ratio of average win to average loss (e.g. 2.0 for 2:1).
    portfolio_value : float
        Total portfolio value in USD.
    current_positions : list
        Existing positions (each with market_value or similar).
    max_position_pct : float
        Maximum single position as fraction of portfolio (default 15%).
    min_position_usd : float
        Minimum position size in USD (default 2000).
    max_positions : int
        Maximum number of positions allowed (default 20).

    Returns
    -------
    dict
        position_size_usd, position_pct, kelly_fraction, can_buy, reasoning
    """
    num_positions = len(current_positions)
    q = 1.0 - win_probability
    b = max(win_loss_ratio, 0.01)

    # Half-Kelly: f* = (bp - q) / 2b
    kelly_raw = (b * win_probability - q) / (2 * b)
    kelly_fraction = max(0.0, min(kelly_raw, max_position_pct))

    raw_size_usd = portfolio_value * kelly_fraction
    position_size_usd = max(min_position_usd, raw_size_usd)
    position_pct = position_size_usd / portfolio_value if portfolio_value > 0 else 0.0

    # Cap by max_position_pct
    max_usd = portfolio_value * max_position_pct
    if position_size_usd > max_usd:
        position_size_usd = max_usd
        position_pct = max_position_pct
        kelly_fraction = max_position_pct

    reasons: list[str] = []
    can_buy = True

    if num_positions >= max_positions:
        can_buy = False
        reasons.append(f"At max positions ({num_positions}/{max_positions})")
    elif portfolio_value <= 0:
        can_buy = False
        reasons.append("No portfolio value")
    elif kelly_raw <= 0:
        can_buy = False
        reasons.append("Kelly fraction ≤ 0 (unfavourable odds)")
    else:
        reasons.append(
            f"Half-Kelly f*={kelly_raw:.2%} → {kelly_fraction:.2%}, "
            f"size=${position_size_usd:,.0f}"
        )

    return {
        "position_size_usd": round(position_size_usd, 2),
        "position_pct": round(position_pct, 4),
        "kelly_fraction": round(kelly_fraction, 4),
        "can_buy": can_buy,
        "reasoning": "; ".join(reasons),
    }
