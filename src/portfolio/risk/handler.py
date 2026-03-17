"""
Lambda handler for portfolio risk guardrails.

Runs guardrails on incoming decisions. Invoked by allocation and execution
before orders are submitted. Pure Python — no LLM.
"""
from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .guardrails import check_all_guardrails, validate_analysis_consistency

_log = get_logger(__name__)

PK_ACCOUNT = "ACCOUNT"
SK_SUMMARY = "SUMMARY"
PK_POSITION = "POSITION"


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


def _load_portfolio_state(portfolio_client: DynamoClient) -> dict[str, Any]:
    """Build portfolio_state from portfolio table."""
    account = portfolio_client.get_item({"pk": PK_ACCOUNT, "sk": SK_SUMMARY})
    cash = _safe_float(account.get("cash_available", 0)) if account else 0.0
    total = _safe_float(account.get("portfolio_value", 0)) if account else 0.0

    positions_raw = portfolio_client.query(Key("pk").eq(PK_POSITION), limit=100)

    positions: list[dict[str, Any]] = []
    sector_value: dict[str, float] = {}

    for item in positions_raw:
        ticker = item.get("sk") or item.get("ticker", "")
        mv = _safe_float(item.get("market_value", 0))
        sector = item.get("sector", "Unknown")
        positions.append({
            "ticker": ticker,
            "market_value": mv,
            "sector": sector,
        })
        sector_value[sector] = sector_value.get(sector, 0) + mv

    if total <= 0 and positions:
        total = sum(p.get("market_value", 0) for p in positions) + cash
    if total <= 0:
        total = cash

    sector_exposure = {
        s: v / total if total > 0 else 0.0
        for s, v in sector_value.items()
    }

    return {
        "portfolio_value": total,
        "cash_available": cash,
        "positions": positions,
        "sector_exposure": sector_exposure,
    }


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
    portfolio_client = DynamoClient(cfg.table_portfolio)
    cost_tracker = CostTracker()
    budget_status = cost_tracker.check_budget()

    portfolio_state = _load_portfolio_state(portfolio_client)

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

        guard_result = check_all_guardrails(proposed, portfolio_state, budget_status)

        # Analysis consistency (BUY only)
        consistency_ok = True
        if signal == "BUY":
            quant_passed = action.get("quant_screen_passed", True)
            if "payload" in action and isinstance(action["payload"], dict):
                quant_passed = action["payload"].get("quant_passed", quant_passed)
            moat = _safe_int(action.get("moat_score", 0))
            mos = _safe_float(action.get("margin_of_safety", 0))
            consistency_ok = validate_analysis_consistency(
                signal, quant_passed, moat, mos
            )
            if not consistency_ok:
                guard_result["violations"] = guard_result.get("violations", []) + [
                    "Circuit breaker: LLM BUY contradicts quant screen"
                ]
                guard_result["passed"] = False

        passed = guard_result.get("passed", False) and consistency_ok
        if not passed:
            overall_passed = False

        results.append({
            "ticker": ticker,
            "signal": signal,
            "passed": passed,
            "violations": guard_result.get("violations", []),
            "consistency_ok": consistency_ok,
        })

    return {
        "passed": overall_passed,
        "results": results,
        "overall_passed": overall_passed,
    }
