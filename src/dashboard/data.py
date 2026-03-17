"""
Data loading for dashboard — DynamoDB and S3.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.dynamo_client import DynamoClient
from shared.s3_client import S3Client


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def load_portfolio() -> dict[str, Any]:
    """Load portfolio summary and positions."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_portfolio)
        account = client.get_item({"pk": "ACCOUNT", "sk": "SUMMARY"})
        positions_raw = client.query(Key("pk").eq("POSITION"), limit=100)
    except Exception:
        return {"cash": 0.0, "portfolio_value": 0.0, "positions": []}

    cash = _safe_float(account.get("cash_available", 0)) if account else 0.0
    total = _safe_float(account.get("portfolio_value", 0)) if account else 0.0
    positions = []
    for p in positions_raw:
        ticker = p.get("sk") or p.get("ticker", "")
        cost = _safe_float(p.get("cost_basis", 0))
        mv = _safe_float(p.get("market_value", 0))
        positions.append({
            "ticker": ticker,
            "shares": _safe_float(p.get("shares", 0)),
            "cost_basis": cost,
            "market_value": mv,
            "sector": p.get("sector", "Unknown"),
            "purchase_date": p.get("purchase_date"),
            "thesis_link": p.get("thesis_link"),
        })
    if total <= 0 and positions:
        total = sum(p["market_value"] for p in positions) + cash
    return {"cash": cash, "portfolio_value": total, "positions": positions}


def load_watchlist_analysis() -> list[dict[str, Any]]:
    """Load watchlist tickers with latest moat/mgmt/IV analysis."""
    try:
        cfg = get_config()
        watchlist_client = DynamoClient(cfg.table_watchlist)
        analysis_client = DynamoClient(cfg.table_analysis)
        watch_items = watchlist_client.scan_all()
        tickers = [i.get("ticker", "").strip().upper() for i in watch_items if i.get("ticker")]
    except Exception:
        return []

    candidates = []
    for ticker in tickers:
        if not ticker:
            continue
        try:
            items = analysis_client.query(Key("ticker").eq(ticker), scan_forward=False, limit=20)
        except Exception:
            continue
        if not items:
            continue
        by_date: dict[str, list] = {}
        for item in items:
            sk = item.get("analysis_date", "")
            date_part = sk.split("#")[0] if "#" in sk else sk
            by_date.setdefault(date_part, []).append(item)
        latest_date = max(by_date.keys()) if by_date else ""
        if not latest_date:
            continue
        merged: dict[str, Any] = {"ticker": ticker, "sector": "Unknown"}
        for item in by_date[latest_date]:
            result = item.get("result") or {}
            if isinstance(result, dict):
                merged.update(result)
            merged["moat_score"] = merged.get("moat_score") or item.get("moat_score")
            merged["management_score"] = merged.get("management_score") or item.get("management_score")
            merged["sector"] = merged.get("sector") or item.get("sector", "Unknown")
        candidates.append(merged)
    return candidates


def load_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """Load recent decisions (buy/sell signals) sorted by timestamp."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_decisions)
        items = client.scan_all()
    except Exception:
        return []
    out = []
    for item in items:
        ts = item.get("timestamp", "")
        if ts:
            out.append(item)
    out.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return out[:limit]


def load_cost_data(months: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load monthly spend and budget status."""
    try:
        tracker = CostTracker()
        from datetime import UTC, datetime

        history = []
        for i in range(months):
            dt = datetime.now(UTC)
            # Go back i months
            year = dt.year
            month = dt.month - i
            while month <= 0:
                month += 12
                year -= 1
            month_key = f"{year}-{month:02d}"
            spent = tracker.get_monthly_spend(month_key)
            history.append({"month": month_key, "spent_usd": float(spent)})
        status = tracker.check_budget()
        return history, status
    except Exception:
        return [], {"budget_usd": 0, "spent_usd": 0, "remaining_usd": 0, "exhausted": False, "utilization_pct": 0}


def load_letter_keys() -> list[str]:
    """List Owner's Letter keys in S3 (letters/...)."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="letters/")
        return sorted(keys, reverse=True)
    except Exception:
        return []


def load_letter_content(key: str) -> str:
    """Load markdown content of a letter."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_markdown(key)
    except Exception:
        return f"*Failed to load {key}*"


def load_postmortem_keys() -> list[str]:
    """List postmortem JSON keys in S3."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="postmortems/")
        return sorted(keys, reverse=True)
    except Exception:
        return []


def load_postmortem(key: str) -> dict[str, Any]:
    """Load postmortem JSON."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_json(key)
    except Exception:
        return {}


def load_lessons() -> list[dict[str, Any]]:
    """Load active lessons from DynamoDB."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_lessons)
        items = client.scan_all(filter_expression=Attr("active").eq(True))
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        return [i for i in items if (i.get("expires_at") or "") > now]
    except Exception:
        return []


def load_config_thresholds() -> dict[str, Any]:
    """Load screening thresholds from config table."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_config)
        item = client.get_item({"config_key": "screening_thresholds"})
        return (item.get("value") or {}) if item else {}
    except Exception:
        return {}
