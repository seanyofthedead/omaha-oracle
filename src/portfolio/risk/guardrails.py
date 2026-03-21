"""
Pure Python guardrails — NO LLM.

Programmatic enforcement of hard limits: position size, sector allocation,
no leverage/shorts/options/crypto, budget exhaustion, cash reserve.
"""

from __future__ import annotations

from typing import Any

from shared.converters import safe_float

MAX_POSITION_PCT = 0.15
MAX_SECTOR_PCT = 0.35
MIN_CASH_RESERVE_PCT = 0.10


def check_all_guardrails(
    proposed_action: dict[str, Any],
    portfolio_state: dict[str, Any],
    budget_status: dict[str, Any],
    pending_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Enforce hard limits on proposed actions.

    Checks:
    - Max single position: 15% at cost
    - Max sector allocation: 35%
    - Zero leverage, zero shorts, zero options, zero crypto
    - LLM budget exhaustion
    - 10% minimum cash reserve

    Parameters
    ----------
    proposed_action : dict
        ticker, signal (BUY/SELL), position_size_usd, sector, position_pct,
        asset_type (must be "equity" or absent), side (buy/sell).
    portfolio_state : dict
        portfolio_value, cash_available, positions, sector_exposure.
    budget_status : dict
        exhausted (bool), budget_usd, spent_usd, etc. from CostTracker.
    pending_decisions : list, optional
        Already-approved BUY decisions earlier in the same batch.  Used to
        prevent two buys in the same sector both passing the 35% limit check
        individually while collectively breaching it.

    Returns
    -------
    dict
        passed: bool
        violations: list of violation messages
    """
    violations: list[str] = []
    signal = (proposed_action.get("signal") or "").upper()
    side = (proposed_action.get("side") or "").lower()
    asset_type = (proposed_action.get("asset_type") or "equity").lower()

    # Zero leverage, shorts, options, crypto
    if asset_type not in ("equity", "stock", ""):
        violations.append(f"Prohibited asset type: {asset_type} (equity only)")
    if side == "short" or proposed_action.get("short", False):
        violations.append("Short selling prohibited")
    if proposed_action.get("options") or proposed_action.get("option"):
        violations.append("Options prohibited")
    if proposed_action.get("crypto") or asset_type == "crypto":
        violations.append("Crypto prohibited")
    if proposed_action.get("leverage", 0) != 0:
        violations.append("Leverage prohibited")

    # LLM budget exhaustion
    if budget_status.get("exhausted", False):
        violations.append("LLM budget exhausted — no new analysis allowed")

    # Cash reserve (10% minimum)
    portfolio_value = safe_float(portfolio_state.get("portfolio_value", 0))
    cash = safe_float(portfolio_state.get("cash_available", 0))
    if portfolio_value > 0:
        cash_pct = cash / portfolio_value
        if cash_pct < MIN_CASH_RESERVE_PCT:
            violations.append(
                f"Cash reserve {cash_pct:.1%} below minimum {MIN_CASH_RESERVE_PCT:.0%}"
            )

    # BUY-specific: max position, max sector
    if signal == "BUY" or side == "buy":
        position_usd = safe_float(proposed_action.get("position_size_usd", 0))
        position_pct = safe_float(proposed_action.get("position_pct", 0))
        if position_usd > 0 and portfolio_value > 0:
            pct = position_usd / portfolio_value
            if pct > MAX_POSITION_PCT:
                violations.append(f"Position {pct:.1%} exceeds max {MAX_POSITION_PCT:.0%}")
        elif position_pct > MAX_POSITION_PCT:
            violations.append(f"Position pct {position_pct:.1%} exceeds max {MAX_POSITION_PCT:.0%}")

        sector = proposed_action.get("sector", "Unknown")
        sector_exposure = safe_float(portfolio_state.get("sector_exposure", {}).get(sector, 0))
        if position_usd > 0 and portfolio_value > 0:
            # Sum existing positions in this sector
            existing_sector_value = sum(
                p.get("market_value", 0)
                for p in portfolio_state.get("positions", [])
                if p.get("sector") == sector
            )
            # Also include pending BUY decisions in this sector from the same batch
            pending_sector_usd = sum(
                safe_float(d.get("position_size_usd", 0))
                for d in (pending_decisions or [])
                if d.get("sector") == sector and (d.get("signal") or "").upper() == "BUY"
            )
            new_sector_value = existing_sector_value + pending_sector_usd + position_usd
            new_sector_pct = new_sector_value / portfolio_value
            if new_sector_pct > MAX_SECTOR_PCT:
                violations.append(
                    f"Sector {sector} would reach {new_sector_pct:.1%} (max {MAX_SECTOR_PCT:.0%})"
                )
        elif sector_exposure >= MAX_SECTOR_PCT:
            violations.append(f"Sector {sector} already at limit ({sector_exposure:.1%})")

    passed = len(violations) == 0
    return {"passed": passed, "violations": violations}


def validate_analysis_consistency(
    llm_signal: str,
    quant_screen_passed: bool,
    moat_score: int,
    margin_of_safety: float,
) -> bool:
    """
    Circuit breaker: reject BUY if LLM contradicts quant screen.

    Returns True if consistent (or not a BUY), False if contradiction.
    """
    llm_signal = (llm_signal or "").upper()
    if llm_signal != "BUY":
        return True

    # BUY requires quant screen passed
    if not quant_screen_passed:
        return False

    # BUY requires moat >= 7 and MoS > 30% (quant thresholds)
    if moat_score < 7:
        return False
    if margin_of_safety <= 0.30:
        return False

    return True
