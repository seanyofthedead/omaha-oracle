"""
Lambda handler for portfolio risk guardrails.

Runs guardrails on incoming decisions. Invoked by allocation and execution
before orders are submitted. Pure Python — no LLM.
"""

from __future__ import annotations

from typing import Any

from shared.config import get_config
from shared.converters import safe_float, safe_int
from shared.cost_tracker import CostTracker
from shared.logger import get_logger
from shared.portfolio_helpers import load_portfolio_state

from .guardrails import check_all_guardrails, validate_analysis_consistency

_log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        decisions: list of {ticker, signal, payload, ...}
        Or: buy_decisions, sell_decisions
        Or: proposed_action (single)

    Output:
        passed: bool — True if all guardrails pass
        results: list of {ticker, signal, passed, violations, consistency_ok}
        overall_passed: bool
    """
    cfg = get_config()
    cost_tracker = CostTracker()
    budget_status = cost_tracker.check_budget()

    portfolio_state = load_portfolio_state(cfg.table_portfolio)

    # Normalise to list of proposed actions
    actions: list[dict[str, Any]] = []
    if "proposed_action" in event:
        actions.append(event["proposed_action"])
    if "decisions" in event:
        for d in event["decisions"]:
            if d.get("signal") in ("BUY", "SELL"):
                actions.append(d)
    if "buy_decisions" in event:
        for d in event["buy_decisions"]:
            if d.get("signal") == "BUY":
                actions.append({**d, "side": "buy"})
    if "sell_decisions" in event:
        for d in event["sell_decisions"]:
            if d.get("signal") == "SELL":
                actions.append({**d, "side": "sell"})

    results: list[dict[str, Any]] = []
    overall_passed = True
    # Track approved BUY decisions so the sector check accounts for same-batch buys
    approved_buys: list[dict[str, Any]] = []

    for action in actions:
        ticker = (action.get("ticker") or "").strip().upper()
        signal = (action.get("signal") or "").upper()
        side = (action.get("side") or ("buy" if signal == "BUY" else "sell")).lower()

        proposed = {
            "ticker": ticker,
            "signal": signal,
            "side": side,
            "position_size_usd": action.get("position_size_usd"),
            "position_pct": action.get("position_pct"),
            "sector": action.get("sector", "Unknown"),
            "asset_type": action.get("asset_type", "equity"),
        }

        guard_result = check_all_guardrails(
            proposed, portfolio_state, dict(budget_status), pending_decisions=approved_buys
        )

        # Analysis consistency (BUY only)
        consistency_ok = True
        if signal == "BUY":
            quant_passed = action.get("quant_screen_passed", False)
            if "payload" in action and isinstance(action["payload"], dict):
                quant_passed = action["payload"].get("quant_passed", quant_passed)
            moat = safe_int(action.get("moat_score", 0))
            mos = safe_float(action.get("margin_of_safety", 0))
            consistency_ok = validate_analysis_consistency(signal, quant_passed, moat, mos)
            if not consistency_ok:
                guard_result["violations"] = guard_result.get("violations", []) + [
                    "Circuit breaker: LLM BUY contradicts quant screen"
                ]
                guard_result["passed"] = False

        passed = guard_result.get("passed", False) and consistency_ok
        if not passed:
            overall_passed = False
        elif signal == "BUY":
            # Track this approved BUY so subsequent actions in the same batch
            # see its sector exposure when evaluating their own sector limits
            approved_buys.append(proposed)

        results.append(
            {
                "ticker": ticker,
                "signal": signal,
                "passed": passed,
                "violations": guard_result.get("violations", []),
                "consistency_ok": consistency_ok,
            }
        )

    return {
        "passed": overall_passed,
        "results": results,
        "overall_passed": overall_passed,
    }
