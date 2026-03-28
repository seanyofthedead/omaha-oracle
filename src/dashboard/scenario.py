"""Scenario analysis — simulate adding a position and check guardrails."""

from __future__ import annotations

from typing import Any


def simulate_position_add(
    portfolio: dict[str, Any],
    ticker: str,
    shares: float,
    price: float,
    sector: str = "Unknown",
) -> dict[str, Any]:
    """Simulate adding a position and return before/after metrics.

    Parameters
    ----------
    portfolio : dict with keys: cash, portfolio_value, positions (list of dicts)
    ticker : stock ticker symbol
    shares : number of shares to buy
    price : price per share
    sector : sector of the stock

    Returns
    -------
    dict with: before (metrics), after (metrics), violations (list of strings), feasible (bool)
    """
    cost = shares * price
    cash_before = portfolio.get("cash", 0)
    portfolio_value = portfolio.get("portfolio_value", 0)
    positions = portfolio.get("positions", [])

    # Before metrics
    cash_pct_before = (cash_before / portfolio_value * 100) if portfolio_value > 0 else 0

    # Sector exposure before
    sector_exposure: dict[str, float] = {}
    for pos in positions:
        s = pos.get("sector", "Unknown")
        sector_exposure[s] = sector_exposure.get(s, 0) + pos.get("market_value", 0)

    # Largest position before
    position_pcts_before = [
        (
            pos.get("ticker", "?"),
            pos.get("market_value", 0) / portfolio_value * 100 if portfolio_value > 0 else 0,
        )
        for pos in positions
    ]
    max_pos_before = max((p[1] for p in position_pcts_before), default=0)

    # After metrics
    new_position_value = cost
    cash_after = cash_before - cost
    # Portfolio value stays the same (cash converted to position)

    cash_pct_after = (cash_after / portfolio_value * 100) if portfolio_value > 0 else 0
    new_position_pct = (new_position_value / portfolio_value * 100) if portfolio_value > 0 else 0

    # Check if ticker already exists
    existing = next((p for p in positions if p.get("ticker", "").upper() == ticker.upper()), None)
    if existing:
        new_position_value += existing.get("market_value", 0)
        new_position_pct = (
            (new_position_value / portfolio_value * 100) if portfolio_value > 0 else 0
        )

    # Sector exposure after
    sector_after = sector_exposure.copy()
    sector_after[sector] = sector_after.get(sector, 0) + cost
    max_sector_pct_before = (
        max((v / portfolio_value * 100 for v in sector_exposure.values()), default=0)
        if portfolio_value > 0
        else 0
    )
    max_sector_pct_after = (
        max((v / portfolio_value * 100 for v in sector_after.values()), default=0)
        if portfolio_value > 0
        else 0
    )
    max_sector_name = (
        max(sector_after, key=sector_after.get, default="N/A") if sector_after else "N/A"
    )

    # Check violations
    violations = []
    if cash_after < 0:
        violations.append(
            f"Insufficient cash: need ${cost:,.2f} but only ${cash_before:,.2f} available"
        )
    if cash_pct_after < 10:
        violations.append(f"Cash reserve violation: {cash_pct_after:.1f}% < 10% minimum")
    if new_position_pct > 15:
        violations.append(
            f"Position concentration: {ticker} would be {new_position_pct:.1f}% > 15% max"
        )
    if max_sector_pct_after > 35:
        violations.append(
            f"Sector concentration: {max_sector_name} would be "
            f"{max_sector_pct_after:.1f}% > 35% max"
        )

    feasible = len(violations) == 0 and cash_after >= 0

    return {
        "before": {
            "cash": cash_before,
            "cash_pct": round(cash_pct_before, 1),
            "max_position_pct": round(max_pos_before, 1),
            "max_sector_pct": round(max_sector_pct_before, 1),
            "position_count": len(positions),
        },
        "after": {
            "cash": cash_after,
            "cash_pct": round(cash_pct_after, 1),
            "new_position_pct": round(new_position_pct, 1),
            "max_sector_pct": round(max_sector_pct_after, 1),
            "max_sector_name": max_sector_name,
            "position_count": len(positions) + (0 if existing else 1),
            "cost": cost,
        },
        "violations": violations,
        "feasible": feasible,
    }
