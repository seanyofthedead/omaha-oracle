"""
Buy/sell evaluation logic for value investing.

BUY: requires ALL of MoS > 30%, moat ≥ 7, mgmt ≥ 6, cash, concentration limits,
     sector whitelist.
SELL: only for thesis broken, extreme overvaluation, fraud. Never for price decline,
      earnings miss, market panic, macro fear. 1-year min hold unless thesis broken.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_THRESHOLDS = {
    "mos_min": 0.30,
    "moat_min": 7,
    "mgmt_min": 6,
    "max_position_pct": 0.15,
    "max_sector_pct": 0.35,
    "max_positions": 20,
    "overvaluation_sell_pct": 1.50,
    "moat_thesis_broken": 5,
    "moat_broken_quarters": 2,
    "min_holding_days": 365,
}


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    if val is None:
        return 0
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def evaluate_buy(
    ticker: str,
    analysis: dict[str, Any],
    portfolio_state: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate whether to BUY a ticker.

    BUY requires ALL: MoS > 30%, moat ≥ 7, mgmt ≥ 6, cash available,
    within concentration limits, sector whitelist.

    Returns
    -------
    dict
        signal: "BUY" | "NO_BUY"
        reasons_pass: list of passed checks
        reasons_fail: list of failed checks
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reasons_pass: list[str] = []
    reasons_fail: list[str] = []

    mos = _safe_float(analysis.get("margin_of_safety", 0))
    if mos > th["mos_min"]:
        reasons_pass.append(f"MoS {mos:.1%} > {th['mos_min']:.0%}")
    else:
        reasons_fail.append(f"MoS {mos:.1%} ≤ {th['mos_min']:.0%}")

    moat = _safe_int(analysis.get("moat_score", 0))
    if moat >= th["moat_min"]:
        reasons_pass.append(f"moat {moat} ≥ {th['moat_min']}")
    else:
        reasons_fail.append(f"moat {moat} < {th['moat_min']}")

    mgmt = _safe_int(analysis.get("management_score", 0))
    if mgmt >= th["mgmt_min"]:
        reasons_pass.append(f"mgmt {mgmt} ≥ {th['mgmt_min']}")
    else:
        reasons_fail.append(f"mgmt {mgmt} < {th['mgmt_min']}")

    cash = _safe_float(portfolio_state.get("cash_available", 0))
    portfolio_value = _safe_float(portfolio_state.get("portfolio_value", 0))
    if cash > 0 and portfolio_value > 0:
        reasons_pass.append(f"Cash ${cash:,.0f} available")
    else:
        reasons_fail.append("No cash available")

    positions = portfolio_state.get("positions", [])
    num_positions = len(positions)
    if num_positions < th["max_positions"]:
        reasons_pass.append(f"Positions {num_positions}/{th['max_positions']}")
    else:
        reasons_fail.append(f"At max positions ({num_positions})")

    sector = analysis.get("sector", "Unknown")
    sector_exposure = portfolio_state.get("sector_exposure", {}).get(sector, 0.0)
    max_sector = th.get("max_sector_pct", 0.35)
    if sector_exposure < max_sector:
        reasons_pass.append(f"Sector {sector} at {sector_exposure:.1%} < {max_sector:.0%}")
    else:
        reasons_fail.append(f"Sector {sector} at limit ({sector_exposure:.1%})")

    allowed_sectors = th.get("sector_whitelist", [])
    if allowed_sectors and sector not in allowed_sectors:
        reasons_fail.append(f"Sector {sector} outside whitelist")
    else:
        reasons_pass.append(f"Sector {sector} in whitelist/not restricted")

    signal = "BUY" if not reasons_fail else "NO_BUY"
    return {
        "signal": signal,
        "reasons_pass": reasons_pass,
        "reasons_fail": reasons_fail,
    }


def evaluate_sell(
    ticker: str,
    position: dict[str, Any],
    latest_analysis: dict[str, Any],
    portfolio_state: dict[str, Any],
    moat_history: list[dict[str, Any]] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate whether to SELL a position.

    SELL only for: thesis broken (moat < 5 for 2 quarters), extreme overvaluation
    (>150% IV), fraud.
    NEVER sell for: price decline, earnings miss, market panic, macro fear.
    1-year minimum holding period unless thesis definitively broken.

    Returns
    -------
    dict
        signal: "SELL" | "HOLD"
        reasons: list of reasons
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []

    purchase_date_str = position.get("purchase_date") or position.get("purchaseDate")
    purchase_date: datetime | None = None
    if purchase_date_str:
        try:
            purchase_date = datetime.fromisoformat(
                str(purchase_date_str).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            pass

    min_hold = timedelta(days=th["min_holding_days"])
    now = datetime.now(UTC)
    holding_period_ok = True
    if purchase_date:
        if purchase_date.tzinfo is None:
            purchase_date = purchase_date.replace(tzinfo=UTC)
        holding_period_ok = (now - purchase_date) >= min_hold

    # Thesis broken: moat < 5 for 2 consecutive quarters
    moat_broken = False
    if moat_history and len(moat_history) >= th["moat_broken_quarters"]:
        recent = sorted(moat_history, key=lambda x: x.get("date", ""), reverse=True)
        recent = recent[: th["moat_broken_quarters"]]
        if all(
            _safe_int(m.get("moat_score", 0)) < th["moat_thesis_broken"]
            for m in recent
        ):
            moat_broken = True
            reasons.append(
                f"Thesis broken: moat < {th['moat_thesis_broken']} for "
                f"{th['moat_broken_quarters']} quarters"
            )

    # Extreme overvaluation: price > 150% of intrinsic value
    iv = _safe_float(latest_analysis.get("intrinsic_value_per_share", 0))
    price = _safe_float(
        latest_analysis.get("current_price")
        or position.get("current_price")
        or position.get("market_value", 0) / max(1, position.get("shares", 1))
    )
    overvalued = False
    if iv > 0 and price > 0:
        premium = price / iv
        if premium >= th["overvaluation_sell_pct"]:
            overvalued = True
            reasons.append(
                f"Extreme overvaluation: price {premium:.1%} of IV "
                f"(>{th['overvaluation_sell_pct']:.0%})"
            )

    # Fraud flag (if analysis exposes it)
    fraud = bool(latest_analysis.get("fraud_red_flags") or position.get("fraud_flag"))
    if fraud:
        reasons.append("Fraud red flags detected")

    # SELL only if one of the valid reasons AND (holding period ok OR thesis broken)
    can_sell = (moat_broken or overvalued or fraud) and (
        holding_period_ok or moat_broken
    )

    if not can_sell:
        if not holding_period_ok and not moat_broken:
            reasons.append(
                f"Min holding period not met ({th['min_holding_days']} days); "
                "HOLD unless thesis broken"
            )
        if not (moat_broken or overvalued or fraud):
            reasons.append(
                "No sell trigger (thesis intact, not overvalued, no fraud); HOLD"
            )

    signal = "SELL" if can_sell else "HOLD"
    return {"signal": signal, "reasons": reasons}
