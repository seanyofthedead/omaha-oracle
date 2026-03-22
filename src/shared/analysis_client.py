"""
Higher-level helpers for reading analysis table results.

Shared between the portfolio allocation Lambda and the Streamlit dashboard.
"""

from __future__ import annotations

from typing import Any


def merge_latest_analysis(
    items: list[dict[str, Any]],
    ticker: str,
) -> dict[str, Any] | None:
    """
    Group *items* by date prefix, take the latest date, and merge result dicts.

    Parameters
    ----------
    items:
        DynamoDB items from the analysis table for a single ticker, ordered
        newest-first (``scan_forward=False``).
    ticker:
        Ticker symbol used to seed the merged dict.

    Returns
    -------
    dict | None
        Merged analysis dict, or ``None`` when *items* is empty.
    """
    if not items:
        return None

    by_date: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sk = item.get("analysis_date", "")
        date_part = sk.split("#")[0] if "#" in sk else sk
        by_date.setdefault(date_part, []).append(item)

    latest_date = max(by_date.keys()) if by_date else ""
    if not latest_date:
        return None

    merged: dict[str, Any] = {"ticker": ticker, "sector": "Unknown"}
    for item in by_date[latest_date]:
        result = item.get("result") or {}
        if isinstance(result, dict):
            merged.update(result)
        merged["moat_score"] = merged.get("moat_score") or item.get("moat_score")
        merged["management_score"] = merged.get("management_score") or item.get("management_score")
        merged["sector"] = merged.get("sector") or item.get("sector", "Unknown")

    return merged


def load_latest_analysis(
    table_name: str,
    ticker: str,
    limit: int = 20,
) -> dict[str, Any] | None:
    """
    Query *table_name* for *ticker* and return the merged latest analysis.

    Parameters
    ----------
    table_name:
        DynamoDB table name for the analysis table.
    ticker:
        Stock ticker to look up.
    limit:
        Maximum number of DynamoDB items to retrieve before merging.

    Returns
    -------
    dict | None
        Merged analysis dict, or ``None`` when no items are found.
    """
    from boto3.dynamodb.conditions import Key

    from shared.dynamo_client import DynamoClient

    client = DynamoClient(table_name)
    items = client.query(
        Key("ticker").eq(ticker),
        scan_forward=False,
        limit=limit,
    )
    return merge_latest_analysis(items, ticker)
