"""
Lambda handler for Yahoo Finance data ingestion.

Event types:
  - {"action": "batch_prices"}           — refresh prices for all watchlist companies
  - {"action": "full_refresh", "ticker": "AAPL"} — refresh one company + 10y weekly history
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

# Fields to extract from yfinance Ticker.info for companies table
_INFO_KEYS = [
    "currentPrice",
    "regularMarketPrice",
    "trailingPE",
    "priceToBook",
    "trailingEps",
    "bookValue",
    "marketCap",
    "sector",
    "industry",
    "targetMeanPrice",
    "targetHighPrice",
    "targetLowPrice",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "beta",
]


def _get_watchlist_tickers(dynamo: DynamoClient) -> list[str]:
    """Return tickers from the watchlist table."""
    items = dynamo.scan_all(projection_expression="ticker")
    return [i["ticker"] for i in items if i.get("ticker")]


def _info_to_item(ticker: str, info: dict[str, Any], updated_at: str) -> dict[str, Any]:
    """Build a DynamoDB item from yfinance info dict."""
    item: dict[str, Any] = {
        "ticker": ticker,
        "updated_at": updated_at,
    }
    for key in _INFO_KEYS:
        val = info.get(key)
        if val is not None:
            # DynamoDB doesn't support float('nan') or float('inf')
            if isinstance(val, float):
                if val != val:  # NaN
                    continue
                if val in (float("inf"), float("-inf")):
                    continue
            item[key] = val
    # Normalise current price if both present
    if "currentPrice" not in item and "regularMarketPrice" in item:
        item["currentPrice"] = item["regularMarketPrice"]
    return item


def _fetch_prices(ticker: str) -> dict[str, Any]:
    """Fetch current price and fundamentals from yfinance."""
    t = yf.Ticker(ticker)
    info = t.info or {}
    return info


def _fetch_weekly_history(ticker: str) -> list[dict[str, Any]]:
    """Fetch 10-year weekly price history. Returns list of OHLCV records."""
    t = yf.Ticker(ticker)
    df = t.history(period="10y", interval="1wk")
    if df.empty:
        return []
    records: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        records.append({
            "date": idx.strftime("%Y-%m-%d")
            if hasattr(idx, "strftime")
            else str(idx),
            "open": float(row["Open"]) if "Open" in row else None,
            "high": float(row["High"]) if "High" in row else None,
            "low": float(row["Low"]) if "Low" in row else None,
            "close": float(row["Close"]) if "Close" in row else None,
            "volume": int(row["Volume"]) if "Volume" in row else None,
        })
    return records


def _process_ticker(
    ticker: str,
    companies: DynamoClient,
    s3: S3Client,
    include_history: bool,
    updated_at: str,
) -> bool:
    """Fetch and store company data. Returns True if successful."""
    info = _fetch_prices(ticker)
    if not info:
        _log.warning("No info returned for ticker", extra={"ticker": ticker})
        return False

    item = _info_to_item(ticker, info, updated_at)
    if len(item) <= 2:  # only ticker + updated_at
        _log.warning("No usable metrics for ticker", extra={"ticker": ticker})
        return False

    companies.put_item(item)

    if include_history:
        history = _fetch_weekly_history(ticker)
        if history:
            s3.write_json(
                f"processed/prices/{ticker}/weekly_10y.json",
                {"ticker": ticker, "observations": history},
            )
            _log.info(
                "Stored weekly history",
                extra={"ticker": ticker, "observations": len(history)},
            )

    return True


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Event schema:
      action: "batch_prices" | "full_refresh"
      ticker: str (required for full_refresh)
    """
    cfg = get_config()
    companies = DynamoClient(cfg.table_companies)
    watchlist = DynamoClient(cfg.table_watchlist)
    s3 = S3Client()
    updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    action = (event.get("action") or "").strip().lower()
    ticker_arg = (event.get("ticker") or "").strip().upper()

    if action == "full_refresh" and not ticker_arg:
        return {"status": "error", "message": "ticker required for action=full_refresh"}

    processed = 0
    errors: list[str] = []

    if action == "batch_prices":
        tickers = _get_watchlist_tickers(watchlist)
        if not tickers:
            _log.warning("Watchlist empty — nothing to process")
            return {"status": "ok", "processed": 0, "message": "watchlist empty"}
        for t in tickers:
            try:
                if _process_ticker(t, companies, s3, include_history=False, updated_at=updated_at):
                    processed += 1
            except Exception as exc:
                errors.append(f"{t}: {exc}")
                _log.exception("Failed to process ticker", extra={"ticker": t})

    elif action == "full_refresh":
        try:
            if _process_ticker(
                ticker_arg, companies, s3, include_history=True, updated_at=updated_at
            ):
                processed = 1
        except Exception as exc:
            _log.exception("Full refresh failed", extra={"ticker": ticker_arg})
            return {"status": "error", "message": str(exc)}

    else:
        return {"status": "error", "message": f"unknown action: {action}"}

    return {
        "status": "ok",
        "action": action,
        "processed": processed,
        "errors": errors[:10],
    }
