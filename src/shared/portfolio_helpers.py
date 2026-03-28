"""
Shared portfolio state loader for Lambda handlers.

Sequential implementation (no ThreadPoolExecutor) — suitable for Lambda
contexts where the two DynamoDB calls are fast enough not to need parallelism.
"""

from __future__ import annotations

from typing import Any

from shared.logger import get_logger

_log = get_logger(__name__)


def load_portfolio_state(table_name: str) -> dict[str, Any]:
    """
    Load portfolio state from a DynamoDB portfolio table.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB portfolio table.

    Returns
    -------
    dict with keys:
        portfolio_value (float): Total portfolio value in USD.
        cash_available (float): Uninvested cash.
        positions (list[dict]): Per-ticker position records.
        sector_exposure (dict[str, float]): Sector → fraction of portfolio.
    """
    from boto3.dynamodb.conditions import Key

    from shared.converters import safe_float
    from shared.dynamo_client import DynamoClient

    client = DynamoClient(table_name)

    try:
        account = client.get_item({"pk": "ACCOUNT", "sk": "SUMMARY"})
    except Exception:
        _log.error(
            "Failed to load account summary from portfolio table",
            extra={"table": table_name},
        )
        raise
    cash = safe_float(account.get("cash_available", 0)) if account else 0.0
    total = safe_float(account.get("portfolio_value", 0)) if account else 0.0

    try:
        positions_raw = client.query(Key("pk").eq("POSITION"))
    except Exception:
        _log.error(
            "Failed to query positions from portfolio table",
            extra={"table": table_name},
        )
        raise
    positions: list[dict[str, Any]] = []
    sector_value: dict[str, float] = {}

    for item in positions_raw:
        ticker = item.get("sk") or item.get("ticker", "")
        mv = safe_float(item.get("market_value", 0))
        sector = item.get("sector", "Unknown")
        positions.append(
            {
                "ticker": ticker,
                "market_value": mv,
                "shares": safe_float(item.get("shares", 0)),
                "sector": sector,
                "cost_basis": safe_float(item.get("cost_basis", 0)),
                "purchase_date": item.get("purchase_date"),
            }
        )
        sector_value[sector] = sector_value.get(sector, 0) + mv

    if total <= 0 and positions:
        total = sum(p["market_value"] for p in positions) + cash
    if total <= 0:
        total = cash

    sector_exposure = {s: v / total if total > 0 else 0.0 for s, v in sector_value.items()}

    return {
        "portfolio_value": total,
        "cash_available": cash,
        "positions": positions,
        "sector_exposure": sector_exposure,
    }
