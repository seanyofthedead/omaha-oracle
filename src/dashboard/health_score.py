"""Portfolio health score — composite 0-100 gauge."""

from __future__ import annotations

from typing import Any


def compute_health_score(portfolio: dict[str, Any]) -> dict[str, Any]:
    """Compute a composite portfolio health score from 0 to 100.

    Components:
    - Cash Reserve (0-25): How much buffer above the 10% minimum
    - Diversification (0-25): How well spread across sectors (vs 35% max)
    - Position Sizing (0-25): No single position > 15%
    - Activity (0-25): Has positions, not 100% cash

    Returns dict with: total_score, components dict, grade (A-F)
    """
    positions = portfolio.get("positions", [])
    cash = portfolio.get("cash", 0)
    portfolio_value = portfolio.get("portfolio_value", 0)

    if portfolio_value <= 0:
        return {
            "total_score": 0,
            "components": {
                "cash_reserve": 0,
                "diversification": 0,
                "position_sizing": 0,
                "activity": 0,
            },
            "grade": "F",
        }

    # 1. Cash Reserve Score (0-25)
    # 10% = threshold, 15% = good, 25%+ = max score
    cash_pct = cash / portfolio_value * 100
    if cash_pct < 10:
        cash_score = max(0, cash_pct / 10 * 15)  # Below minimum = poor
    elif cash_pct <= 30:
        cash_score = 15 + (cash_pct - 10) / 20 * 10  # 10-30% = good to great
    else:
        cash_score = 20  # Too much cash = slightly penalized
    cash_score = min(25, cash_score)

    # 2. Diversification Score (0-25)
    # Count sectors and check concentration
    sectors: dict[str, float] = {}
    for pos in positions:
        sector = pos.get("sector", "Unknown")
        value = pos.get("market_value", 0)
        sectors[sector] = sectors.get(sector, 0) + value

    invested = portfolio_value - cash
    if invested > 0 and sectors:
        max_sector_pct = max(v / portfolio_value * 100 for v in sectors.values())
        num_sectors = len(sectors)

        # Penalize if any sector > 35%
        if max_sector_pct > 35:
            concentration_score = max(0, 10 - (max_sector_pct - 35))
        else:
            concentration_score = 10 + (35 - max_sector_pct) / 35 * 5

        # Reward more sectors
        sector_count_score = min(10, num_sectors * 2.5)
        diversification_score = min(25, concentration_score + sector_count_score)
    else:
        diversification_score = 0

    # 3. Position Sizing Score (0-25)
    if positions and invested > 0:
        position_pcts = [pos.get("market_value", 0) / portfolio_value * 100 for pos in positions]
        max_pos_pct = max(position_pcts) if position_pcts else 0
        violations = sum(1 for p in position_pcts if p > 15)

        if violations > 0:
            sizing_score = max(0.0, 15 - violations * 5)
        elif max_pos_pct <= 15:
            sizing_score = 20 + (15 - max_pos_pct) / 15 * 5
        else:
            sizing_score = 15.0
        sizing_score = min(25.0, sizing_score)
    else:
        sizing_score = 0.0

    # 4. Activity Score (0-25)
    num_positions = len(positions)
    if num_positions == 0:
        activity_score = 5  # All cash — not great
    elif num_positions <= 3:
        activity_score = 10 + num_positions * 2
    elif num_positions <= 15:
        activity_score = 16 + min(9, num_positions)
    else:
        activity_score = 22  # Slightly penalize too many positions
    activity_score = min(25, activity_score)

    total = round(cash_score + diversification_score + sizing_score + activity_score)
    total = max(0, min(100, total))

    # Grade
    if total >= 90:
        grade = "A"
    elif total >= 75:
        grade = "B"
    elif total >= 60:
        grade = "C"
    elif total >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "total_score": total,
        "components": {
            "cash_reserve": round(cash_score),
            "diversification": round(diversification_score),
            "position_sizing": round(sizing_score),
            "activity": round(activity_score),
        },
        "grade": grade,
    }
