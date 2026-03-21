"""
Data loading for dashboard — DynamoDB and S3.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.analysis_client import merge_latest_analysis
from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger
from shared.portfolio_helpers import load_portfolio_state
from shared.s3_client import S3Client

_log = get_logger(__name__)


def load_portfolio() -> dict[str, Any]:
    """Load portfolio summary and positions."""
    try:
        cfg = get_config()
        state = load_portfolio_state(cfg.table_portfolio)
    except Exception as exc:
        _log.warning("load_portfolio failed", extra={"error": str(exc)})
        return {"cash": 0.0, "portfolio_value": 0.0, "positions": []}

    return {
        "cash": state["cash_available"],
        "portfolio_value": state["portfolio_value"],
        "positions": state["positions"],
    }


def load_watchlist_analysis() -> list[dict[str, Any]]:
    """Load watchlist tickers with latest moat/mgmt/IV analysis."""
    from concurrent.futures import ThreadPoolExecutor

    try:
        cfg = get_config()
        watchlist_client = DynamoClient(cfg.table_watchlist)
        analysis_client = DynamoClient(cfg.table_analysis)
        watch_items = watchlist_client.scan_all()
        tickers = [i.get("ticker", "").strip().upper() for i in watch_items if i.get("ticker")]
    except Exception as exc:
        _log.warning("load_watchlist_analysis failed", extra={"error": str(exc)})
        return []

    if not tickers:
        return []

    try:
        with ThreadPoolExecutor(max_workers=min(len(tickers), 10)) as ex:
            results = list(
                ex.map(
                    lambda t: analysis_client.query(
                        Key("ticker").eq(t), scan_forward=False, limit=20
                    ),
                    tickers,
                )
            )
        all_items = [item for ticker_items in results for item in ticker_items]
    except Exception as exc:
        _log.warning("load_watchlist_analysis analysis query failed", extra={"error": str(exc)})
        return []

    tickers_set = set(tickers)

    by_ticker: dict[str, list] = {}
    for item in all_items:
        t = item.get("ticker", "").strip().upper()
        if t in tickers_set:
            by_ticker.setdefault(t, []).append(item)

    candidates = []
    for ticker in tickers:
        if not ticker:
            continue
        items = by_ticker.get(ticker)
        if not items:
            continue
        merged = merge_latest_analysis(items, ticker)
        if merged:
            candidates.append(merged)
    return candidates


def load_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """Load recent decisions (buy/sell signals) sorted by timestamp."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_decisions)
        items = client.query(
            Key("record_type").eq("DECISION"),
            index_name="record_type-timestamp-index",
            scan_forward=False,
            limit=limit,
        )
    except Exception as exc:
        _log.warning("load_decisions failed", extra={"error": str(exc)})
        return []
    return items


def load_cost_data(months: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load monthly spend and budget status."""
    try:
        tracker = CostTracker()
        from datetime import UTC, datetime

        dt = datetime.now(UTC)
        month_keys = []
        for i in range(months):
            year = dt.year
            month = dt.month - i
            while month <= 0:
                month += 12
                year -= 1
            month_keys.append(f"{year}-{month:02d}")
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_history = ex.submit(tracker.get_spend_history, month_keys)
            f_budget = ex.submit(tracker.check_budget)
        spend_by_month = f_history.result()
        status = f_budget.result()
        history = [{"month": mk, "spent_usd": spend_by_month.get(mk, 0.0)} for mk in month_keys]
        return history, status
    except Exception as exc:
        _log.warning("load_cost_data failed", extra={"error": str(exc)})
        return [], {
            "budget_usd": 0,
            "spent_usd": 0,
            "remaining_usd": 0,
            "exhausted": False,
            "utilization_pct": 0,
        }


def load_letter_keys() -> list[str]:
    """List Owner's Letter keys in S3 (letters/...)."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="letters/")
        return sorted(keys, reverse=True)
    except Exception as exc:
        _log.warning("load_letter_keys failed", extra={"error": str(exc)})
        return []


def load_letter_content(key: str) -> str:
    """Load markdown content of a letter."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_markdown(key)
    except Exception as exc:
        _log.warning("load_letter_content failed", extra={"key": key, "error": str(exc)})
        return f"*Failed to load {key}*"


def load_postmortem_keys() -> list[str]:
    """List postmortem JSON keys in S3."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="postmortems/")
        return sorted(keys, reverse=True)
    except Exception as exc:
        _log.warning("load_postmortem_keys failed", extra={"error": str(exc)})
        return []


def load_postmortem(key: str) -> dict[str, Any]:
    """Load postmortem JSON."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_json(key)
    except Exception as exc:
        _log.warning("load_postmortem failed", extra={"key": key, "error": str(exc)})
        return {}


def load_lessons() -> list[dict[str, Any]]:
    """Load active lessons from DynamoDB."""
    try:
        from datetime import UTC, datetime

        cfg = get_config()
        client = DynamoClient(cfg.table_lessons)
        now = datetime.now(UTC).isoformat()
        return client.query(
            Key("active_flag").eq("1") & Key("expires_at").gt(now),
            index_name="active_flag-expires_at-index",
        )
    except Exception as exc:
        _log.warning("load_lessons failed", extra={"error": str(exc)})
        return []


def load_config_thresholds() -> dict[str, Any]:
    """Load screening thresholds from config table."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_config)
        item = client.get_item({"config_key": "screening_thresholds"})
        return (item.get("value") or {}) if item else {}
    except Exception as exc:
        _log.warning("load_config_thresholds failed", extra={"error": str(exc)})
        return {}
