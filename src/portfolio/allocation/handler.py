"""
Lambda handler for portfolio allocation.

Processes buy candidates from analysis pipeline, evaluates existing positions
for sell signals, logs all decisions to the decisions DynamoDB table.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .buy_sell_logic import evaluate_buy, evaluate_sell
from .position_sizer import calculate_position_size

_log = get_logger(__name__)

# Portfolio table key layout
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

    positions_raw = portfolio_client.query(
        Key("pk").eq(PK_POSITION),
        limit=100,
    )
    positions: list[dict[str, Any]] = []
    sector_value: dict[str, float] = {}

    for item in positions_raw:
        ticker = item.get("sk") or item.get("ticker", "")
        mv = _safe_float(item.get("market_value", 0))
        shares = _safe_float(item.get("shares", 0))
        sector = item.get("sector", "Unknown")
        positions.append({
            "ticker": ticker,
            "market_value": mv,
            "shares": shares,
            "sector": sector,
            "cost_basis": _safe_float(item.get("cost_basis", 0)),
            "purchase_date": item.get("purchase_date"),
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


def _load_latest_analysis(
    analysis_client: DynamoClient,
    ticker: str,
) -> dict[str, Any] | None:
    """Fetch and merge latest analysis for a ticker."""
    items = analysis_client.query(
        Key("ticker").eq(ticker),
        scan_forward=False,
        limit=20,
    )
    if not items:
        return None

    # Group by date (prefix of analysis_date)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sk = item.get("analysis_date", "")
        date_part = sk.split("#")[0] if "#" in sk else sk
        if date_part not in by_date:
            by_date[date_part] = []
        by_date[date_part].append(item)

    latest_date = max(by_date.keys()) if by_date else ""
    if not latest_date:
        return None

    merged: dict[str, Any] = {"ticker": ticker, "sector": "Unknown"}
    for item in by_date[latest_date]:
        result = item.get("result") or {}
        if isinstance(result, dict):
            merged.update(result)
        merged["moat_score"] = merged.get("moat_score") or item.get("moat_score")
        merged["management_score"] = (
            merged.get("management_score") or item.get("management_score")
        )
        merged["sector"] = merged.get("sector") or item.get("sector", "Unknown")

    return merged


def _load_buy_candidates(
    analysis_client: DynamoClient,
    watchlist_client: DynamoClient,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    """Get buy candidates: either from event or by scanning watchlist + analysis."""
    if "tickers" in event and event["tickers"]:
        tickers = [t.strip().upper() for t in event["tickers"] if t]
    elif "analysis_results" in event:
        results = event["analysis_results"]
        if isinstance(results, dict):
            return [
                {"ticker": k, **v} if isinstance(v, dict) else {"ticker": k}
                for k, v in results.items()
            ]
        return []
    else:
        # Scan watchlist
        watchlist_items = watchlist_client.scan_all()
        tickers = [i.get("ticker", "").strip().upper() for i in watchlist_items if i.get("ticker")]

    candidates: list[dict[str, Any]] = []
    for ticker in tickers:
        if not ticker:
            continue
        analysis = _load_latest_analysis(analysis_client, ticker)
        if analysis:
            candidates.append(analysis)
    return candidates


def _log_decision(
    decisions_client: DynamoClient,
    decision_type: str,
    ticker: str,
    signal: str,
    payload: dict[str, Any],
) -> None:
    """Write decision to decisions table."""
    decision_id = f"{decision_type}#{ticker}#{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).isoformat()
    item = {
        "decision_id": decision_id,
        "timestamp": timestamp,
        "decision_type": decision_type,
        "ticker": ticker,
        "signal": signal,
        "payload": payload,
    }
    decisions_client.put_item(item)
    _log.info(
        "decision_logged",
        extra={"ticker": ticker, "signal": signal, "decision_type": decision_type},
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        tickers: optional list of tickers to evaluate
        analysis_results: optional pre-aggregated analysis by ticker
        thresholds: optional override for buy/sell thresholds

    Output:
        buy_decisions: list of BUY/NO_BUY decisions
        sell_decisions: list of SELL/HOLD decisions
        decisions_logged: count
    """
    cfg = get_config()
    portfolio_client = DynamoClient(cfg.table_portfolio)
    analysis_client = DynamoClient(cfg.table_analysis)
    watchlist_client = DynamoClient(cfg.table_watchlist)
    decisions_client = DynamoClient(cfg.table_decisions)

    thresholds = event.get("thresholds") or {}

    portfolio_state = _load_portfolio_state(portfolio_client)
    buy_candidates = _load_buy_candidates(analysis_client, watchlist_client, event)

    buy_decisions: list[dict[str, Any]] = []
    sell_decisions: list[dict[str, Any]] = []
    decisions_logged = 0

    # Evaluate buy candidates
    for analysis in buy_candidates:
        ticker = analysis.get("ticker", "").strip().upper()
        if not ticker:
            continue

        buy_result = evaluate_buy(ticker, analysis, portfolio_state, thresholds)
        signal = buy_result["signal"]

        if signal == "BUY":
            # Position size
            mos = _safe_float(analysis.get("margin_of_safety", 0.3))
            win_prob = min(0.9, max(0.5, 0.5 + mos))
            win_loss = 2.0
            sizing = calculate_position_size(
                win_probability=win_prob,
                win_loss_ratio=win_loss,
                portfolio_value=portfolio_state["portfolio_value"],
                current_positions=portfolio_state["positions"],
                max_position_pct=thresholds.get("max_position_pct", 0.15),
                min_position_usd=thresholds.get("min_position_usd", 2000),
                max_positions=thresholds.get("max_positions", 20),
            )
            if not sizing.get("can_buy", True):
                buy_result["signal"] = "NO_BUY"
                buy_result["reasons_fail"] = buy_result.get("reasons_fail", []) + [
                    sizing.get("reasoning", "Position sizing failed")
                ]
            else:
                buy_result["position_size_usd"] = sizing["position_size_usd"]
                buy_result["position_pct"] = sizing["position_pct"]

        payload = {
            "signal": buy_result["signal"],
            "reasons_pass": buy_result.get("reasons_pass", []),
            "reasons_fail": buy_result.get("reasons_fail", []),
            "position_size_usd": buy_result.get("position_size_usd"),
            "position_pct": buy_result.get("position_pct"),
        }
        _log_decision(decisions_client, "BUY", ticker, buy_result["signal"], payload)
        decisions_logged += 1
        buy_decisions.append({"ticker": ticker, **buy_result})

    # Evaluate existing positions for sell
    for position in portfolio_state.get("positions", []):
        ticker = position.get("ticker", "").strip().upper()
        if not ticker:
            continue

        analysis = _load_latest_analysis(analysis_client, ticker)
        if not analysis:
            continue

        moat_history = []
        # Could fetch moat history from analysis table for prior quarters
        sell_result = evaluate_sell(
            ticker,
            position,
            analysis,
            portfolio_state,
            moat_history=moat_history,
            thresholds=thresholds,
        )

        payload = {
            "signal": sell_result["signal"],
            "reasons": sell_result.get("reasons", []),
        }
        _log_decision(decisions_client, "SELL", ticker, sell_result["signal"], payload)
        decisions_logged += 1
        sell_decisions.append({"ticker": ticker, **sell_result})

    return {
        "buy_decisions": buy_decisions,
        "sell_decisions": sell_decisions,
        "decisions_logged": decisions_logged,
        "portfolio_state": {
            "portfolio_value": portfolio_state["portfolio_value"],
            "cash_available": portfolio_state["cash_available"],
            "num_positions": len(portfolio_state["positions"]),
        },
    }
