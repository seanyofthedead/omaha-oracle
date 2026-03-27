"""
Outcome audit logic — pure data transformation, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import yfinance as yf
from boto3.dynamodb.conditions import Key

from shared.converters import safe_float
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

# Outcome classification thresholds
GOOD_BUY_GAIN_PCT = 0.15
BAD_BUY_LOSS_PCT = 0.20
GOOD_SELL_DROP_PCT = 0.10
BAD_SELL_RISE_PCT = 0.20
MISSED_OPP_RISE_PCT = 0.30


def _quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the quarter."""
    month_start = (quarter - 1) * 3 + 1
    start = datetime(year, month_start, 1, tzinfo=UTC)
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(seconds=1)
    else:
        end = datetime(year, month_start + 3, 1, tzinfo=UTC) - timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _fetch_price(ticker: str, date: datetime | None = None) -> float | None:
    """Fetch price for ticker at date (or current if date is None).

    Returns None on failure so callers can mark outcome as UNCLASSIFIABLE
    rather than computing a return against a bogus 0.0 price.
    """
    try:
        t = yf.Ticker(ticker)
        if date:
            end = date + timedelta(days=1)
            df = t.history(start=date.date(), end=end.date())
            if df.empty:
                info = t.info or {}
                val = safe_float(info.get("previousClose") or info.get("regularMarketPrice"))
                return val if val > 0 else None
            return float(df["Close"].iloc[-1])
        info = t.info or {}
        val = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        return val if val > 0 else None
    except Exception as exc:
        _log.warning("Price fetch failed", extra={"ticker": ticker, "error": str(exc)})
        return None


def _classify_outcome(
    signal: str,
    price_at_decision: float,
    current_price: float,
) -> str:
    """Classify decision outcome."""
    if price_at_decision <= 0:
        return "UNKNOWN"
    if signal == "BUY":
        ret = (current_price - price_at_decision) / price_at_decision
        if ret >= GOOD_BUY_GAIN_PCT:
            return "GOOD_BUY"
        if ret <= -BAD_BUY_LOSS_PCT:
            return "BAD_BUY"
        return "NEUTRAL_BUY"
    if signal == "SELL":
        ret = (current_price - price_at_decision) / price_at_decision
        if ret <= -GOOD_SELL_DROP_PCT:
            return "GOOD_SELL"
        if ret >= BAD_SELL_RISE_PCT:
            return "BAD_SELL"
        return "NEUTRAL_SELL"
    if signal == "NO_BUY":
        if current_price > 0 and price_at_decision > 0:
            ret = (current_price - price_at_decision) / price_at_decision
            if ret >= MISSED_OPP_RISE_PCT:
                return "MISSED_OPPORTUNITY"
        return "CORRECT_PASS"
    return "UNKNOWN"


def run_outcome_audit(
    decisions_client: DynamoClient,
    year: int,
    quarter: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Phase 1: Scan decisions, compare prices, classify outcomes."""
    start_iso, end_iso = _quarter_bounds(year, quarter)
    items = decisions_client.query(
        Key("record_type").eq("DECISION") & Key("timestamp").between(start_iso, end_iso),
        index_name="record_type-timestamp-index",
    )

    # --- Collect unique tickers and (ticker, date) pairs needing prices ---
    ticker_ts_map: dict[str, tuple[str, dict[str, Any]]] = {}  # ticker -> (ts_str, payload)
    for item in items:
        ticker = (item.get("ticker") or "").strip().upper()
        payload = item.get("payload") or {}
        if not ticker:
            ticker = (payload.get("ticker") or "").strip().upper()
        if ticker:
            ticker_ts_map[ticker] = (item.get("timestamp", ""), payload)

    unique_tickers = list(ticker_ts_map.keys())

    # Batch-fetch current prices for all tickers in one yfinance call
    current_cache: dict[str, float] = {}
    if unique_tickers:
        try:
            import pandas as pd

            dl = yf.download(unique_tickers, period="1d", progress=False, auto_adjust=True)
            close = dl["Close"] if "Close" in dl.columns else dl
            if hasattr(close, "iloc") and not close.empty:
                last_row = close.iloc[-1]
                if hasattr(last_row, "items"):
                    for t, price in last_row.items():
                        if pd.notna(price):
                            current_cache[str(t).upper()] = float(price)
                else:
                    # Single ticker returns a Series without ticker dimension
                    t = unique_tickers[0]
                    if pd.notna(last_row):
                        current_cache[t] = float(last_row)
        except Exception as exc:
            _log.warning(
                "Batch price fetch failed, will fall back per-ticker", extra={"error": str(exc)}
            )

    # Cache for historical prices: (ticker, date_str) -> float
    hist_cache: dict[tuple[str, str], float] = {}

    audits: list[dict[str, Any]] = []
    sector_mistakes: dict[str, int] = {}
    bad_buy_moat_scores: list[float] = []
    mistake_count = 0

    for item in items:
        signal = (item.get("signal") or "").upper()
        decision_type = (item.get("decision_type") or "").upper()
        ticker = (item.get("ticker") or "").strip().upper()
        payload = item.get("payload") or {}
        if not ticker:
            ticker = (payload.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        ts_str = item.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = datetime.now(UTC)

        price_at = safe_float(
            payload.get("price_at_decision")
            or payload.get("limit_price")
            or payload.get("current_price")
        )
        if price_at <= 0:
            cache_key = (ticker, ts.date().isoformat())
            if cache_key not in hist_cache:
                hist_cache[cache_key] = _fetch_price(ticker, ts)
            fetched = hist_cache[cache_key]
            price_at = fetched if fetched is not None else 0.0

        # Use cached current price; fall back to per-ticker fetch only if missing
        if ticker in current_cache:
            current_price = current_cache[ticker]
        else:
            fetched_current = _fetch_price(ticker, None)
            current_price = fetched_current if fetched_current is not None else None
            current_cache[ticker] = current_price

        if signal not in ("BUY", "SELL"):
            if decision_type == "ORDER":
                signal = "BUY" if (item.get("side") or "").lower() == "buy" else "SELL"
            elif decision_type == "BUY":
                signal = "NO_BUY" if payload.get("signal") == "NO_BUY" else "BUY"
            elif decision_type == "SELL":
                signal = "SELL" if payload.get("signal") == "SELL" else "HOLD"

        if current_price is None:
            outcome = "UNCLASSIFIABLE"
        else:
            outcome = _classify_outcome(signal, price_at, current_price)
        if outcome not in ("UNCLASSIFIABLE", "UNKNOWN") and outcome in (
            "BAD_BUY",
            "BAD_SELL",
            "MISSED_OPPORTUNITY",
        ):
            mistake_count += 1
            sector_mistakes["Unknown"] = sector_mistakes.get("Unknown", 0) + 1
            if outcome == "BAD_BUY":
                bad_buy_moat_scores.append(safe_float(payload.get("moat_score")))

        # Summarize prediction outcomes if present
        prediction_summary = None
        preds = payload.get("predictions")
        if preds and isinstance(preds, list):
            pred_confirmed = sum(1 for p in preds if isinstance(p, dict) and p.get("status") == "CONFIRMED")
            pred_falsified = sum(1 for p in preds if isinstance(p, dict) and p.get("status") == "FALSIFIED")
            pred_pending = sum(1 for p in preds if isinstance(p, dict) and p.get("status") == "pending")
            pred_unresolvable = sum(1 for p in preds if isinstance(p, dict) and p.get("status") == "UNRESOLVABLE")
            prediction_summary = {
                "total": len(preds),
                "confirmed": pred_confirmed,
                "falsified": pred_falsified,
                "pending": pred_pending,
                "unresolvable": pred_unresolvable,
            }

        audits.append(
            {
                "decision_id": item.get("decision_id"),
                "ticker": ticker,
                "signal": signal,
                "timestamp": ts_str,
                "price_at_decision": price_at,
                "current_price": current_price,  # may be None if price fetch failed
                "outcome": outcome,
                "payload": payload,
                "prediction_summary": prediction_summary,
            }
        )

    # Aggregate prediction accuracy across all decisions
    total_preds = 0
    total_confirmed = 0
    total_falsified = 0
    for audit in audits:
        ps = audit.get("prediction_summary")
        if ps:
            total_confirmed += ps["confirmed"]
            total_falsified += ps["falsified"]
            total_preds += ps["total"]

    summary = {
        "total_decisions": len(audits),
        "mistake_rate": mistake_count / max(len(audits), 1),
        "sector_mistakes": sector_mistakes,
        "avg_moat_score_on_bad_buys": (
            sum(bad_buy_moat_scores) / len(bad_buy_moat_scores) if bad_buy_moat_scores else 0
        ),
        "prediction_stats": {
            "total_predictions": total_preds,
            "confirmed": total_confirmed,
            "falsified": total_falsified,
            "accuracy": (
                total_confirmed / (total_confirmed + total_falsified)
                if (total_confirmed + total_falsified) > 0
                else None
            ),
        },
    }
    return audits, summary
