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

from shared.analysis_client import load_latest_analysis
from shared.config import get_config
from shared.converters import safe_float
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger
from shared.portfolio_helpers import load_portfolio_state

from .buy_sell_logic import evaluate_buy, evaluate_sell
from .position_sizer import calculate_position_size

_log = get_logger(__name__)


def _check_trading_enabled(config_client: DynamoClient) -> None:
    """
    Raise RuntimeError if the kill switch has been flipped.

    Flip via:
        aws dynamodb put-item --table-name omaha-oracle-prod-config \
          --item '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"},"value":{"BOOL":false}}'
    """
    item = config_client.get_item({"config_key": "trading_enabled"})
    if item is not None and item.get("value") is False:
        raise RuntimeError(
            "Trading is disabled via kill switch (config.trading_enabled=false). "
            "Set value to true to resume."
        )


def _load_moat_history(
    analysis_client: DynamoClient,
    ticker: str,
    quarters: int = 2,
) -> list[dict[str, Any]]:
    """Return the last *quarters* moat scores for *ticker* from the analysis table."""
    items = analysis_client.query(
        Key("ticker").eq(ticker),
        scan_forward=False,
        limit=quarters * 5,  # over-fetch to handle multiple records per quarter
    )
    seen_dates: set[str] = set()
    history: list[dict[str, Any]] = []
    for item in items:
        sk = item.get("analysis_date", "")
        date_part = sk.split("#")[0] if "#" in sk else sk
        if date_part in seen_dates:
            continue
        result = item.get("result") or {}
        moat = item.get("moat_score") or (
            result.get("moat_score") if isinstance(result, dict) else None
        )
        if moat is not None:
            seen_dates.add(date_part)
            history.append({"date": date_part, "moat_score": moat})
            if len(history) >= quarters:
                break
    return history


def _load_buy_candidates(
    analysis_client: DynamoClient,
    watchlist_client: DynamoClient,
    event: dict[str, Any],
    analysis_cache: dict[str, Any],
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
        if ticker not in analysis_cache:
            analysis_cache[ticker] = load_latest_analysis(analysis_client._table_name, ticker)
        analysis = analysis_cache[ticker]
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
        "record_type": "DECISION",
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
    config_client = DynamoClient(cfg.table_config)
    _check_trading_enabled(config_client)

    analysis_client = DynamoClient(cfg.table_analysis)
    watchlist_client = DynamoClient(cfg.table_watchlist)
    decisions_client = DynamoClient(cfg.table_decisions)

    thresholds = event.get("thresholds") or {}

    portfolio_state = load_portfolio_state(cfg.table_portfolio)
    # Shared cache: avoids repeated DynamoDB reads for the same ticker across buy + sell loops
    analysis_cache: dict[str, Any] = {}
    buy_candidates = _load_buy_candidates(analysis_client, watchlist_client, event, analysis_cache)

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
            mos = safe_float(analysis.get("margin_of_safety", 0.3))
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

        # Attach predictions from thesis generator (if any) for decision journaling
        if buy_result["signal"] == "BUY":
            raw_predictions = analysis.get("predictions") or []
            pred_prefix = f"pred_{ticker}_{uuid.uuid4().hex[:8]}"
            predictions_with_status = []
            for i, pred in enumerate(raw_predictions):
                if isinstance(pred, dict):
                    predictions_with_status.append(
                        {
                            **pred,
                            "id": f"{pred_prefix}_{i}",
                            "status": "pending",
                        }
                    )
            payload["predictions"] = predictions_with_status

        _log_decision(decisions_client, "BUY", ticker, buy_result["signal"], payload)
        decisions_logged += 1
        buy_decisions.append({"ticker": ticker, **buy_result})

    # Evaluate existing positions for sell
    for position in portfolio_state.get("positions", []):
        ticker = position.get("ticker", "").strip().upper()
        if not ticker:
            continue

        if ticker not in analysis_cache:
            analysis_cache[ticker] = load_latest_analysis(cfg.table_analysis, ticker)
        analysis = analysis_cache[ticker]
        if not analysis:
            continue

        moat_history = _load_moat_history(analysis_client, ticker)
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
