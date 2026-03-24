"""
Data loading for dashboard — DynamoDB and S3.

Every public function is cached with ``@st.cache_data`` and an explicit TTL
so repeat visits within the window are instant.

Functions raise ``DataLoadError`` on failure so views can show actionable
error messages.  ``@st.cache_data`` does **not** cache exceptions — a failing
call will be retried on the next Streamlit rerun.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import streamlit as st
from boto3.dynamodb.conditions import Key
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from shared.analysis_client import merge_latest_analysis
from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger
from shared.portfolio_helpers import load_portfolio_state
from shared.s3_client import S3Client

_log = get_logger(__name__)

# ── TTL constants (seconds) ──────────────────────────────────────────────
_TTL_MARKET = 300  # 5 min  — portfolio positions, decisions
_TTL_ANALYSIS = 600  # 10 min — watchlist analysis, lessons, cost data
_TTL_STATIC = 3600  # 1 hour — letters, postmortems, config thresholds


class DataLoadError(Exception):
    """Raised when a dashboard data-loading function fails.

    Carries a user-facing message (no tracebacks) that views can pass
    directly to ``st.error()``.
    """


def _friendly_aws_message(exc: Exception, resource: str) -> str:
    """Turn an AWS/network exception into a user-friendly sentence."""
    if isinstance(exc, NoCredentialsError):
        return (
            f"Could not load {resource}: AWS credentials are missing. "
            "Check your AWS_PROFILE or environment variables."
        )
    if isinstance(exc, EndpointConnectionError):
        return (
            f"Could not load {resource}: unable to reach AWS. "
            "Check your network connection and VPN."
        )
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return (
                f"Could not load {resource}: the DynamoDB table does not exist. "
                "Run 'cdk deploy' to create infrastructure."
            )
        if code in ("AccessDeniedException", "UnauthorizedAccess"):
            return f"Could not load {resource}: access denied. Check your IAM permissions."
        return f"Could not load {resource}: AWS error ({code}). Try again shortly."
    return f"Could not load {resource}: {type(exc).__name__}. Try again shortly."


@st.cache_data(ttl=_TTL_MARKET, show_spinner=False)
def load_portfolio() -> dict[str, Any]:
    """Load portfolio summary and positions."""
    try:
        cfg = get_config()
        state = load_portfolio_state(cfg.table_portfolio)
    except Exception as exc:
        _log.warning("load_portfolio failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "portfolio positions")) from exc

    return {
        "cash": state["cash_available"],
        "portfolio_value": state["portfolio_value"],
        "positions": state["positions"],
    }


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_watchlist_analysis() -> list[dict[str, Any]]:
    """Load watchlist tickers with latest moat/mgmt/IV analysis."""
    try:
        cfg = get_config()
        watchlist_client = DynamoClient(cfg.table_watchlist)
        analysis_client = DynamoClient(cfg.table_analysis)
        watch_items = watchlist_client.scan_all()
        tickers = [i.get("ticker", "").strip().upper() for i in watch_items if i.get("ticker")]
    except Exception as exc:
        _log.warning("load_watchlist_analysis failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "watchlist data")) from exc

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
        raise DataLoadError(
            _friendly_aws_message(exc, "analysis results for watchlist tickers")
        ) from exc

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


@st.cache_data(ttl=_TTL_MARKET, show_spinner=False)
def load_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """Load recent decisions (buy/sell signals) sorted by timestamp."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_decisions)
        return client.query(
            Key("record_type").eq("DECISION"),
            index_name="record_type-timestamp-index",
            scan_forward=False,
            limit=limit,
        )
    except Exception as exc:
        _log.warning("load_decisions failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "buy/sell decisions")) from exc


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_cost_data(months: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load monthly spend and budget status."""
    try:
        tracker = CostTracker()
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
        raise DataLoadError(_friendly_aws_message(exc, "LLM cost data")) from exc


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_letter_keys() -> list[str]:
    """List Owner's Letter keys in S3 (letters/...)."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="letters/")
        return sorted(keys, reverse=True)
    except Exception as exc:
        _log.warning("load_letter_keys failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "owner's letter archive")) from exc


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_letter_content(key: str) -> str:
    """Load markdown content of a letter."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_markdown(key)
    except Exception as exc:
        _log.warning("load_letter_content failed", extra={"key": key, "error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, f"letter '{key}'")) from exc


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_postmortem_keys() -> list[str]:
    """List postmortem JSON keys in S3."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        keys = s3.list_keys(prefix="postmortems/")
        return sorted(keys, reverse=True)
    except Exception as exc:
        _log.warning("load_postmortem_keys failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "postmortem archive")) from exc


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_postmortem(key: str) -> dict[str, Any]:
    """Load postmortem JSON."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        return s3.read_json(key)
    except Exception as exc:
        _log.warning("load_postmortem failed", extra={"key": key, "error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, f"postmortem '{key}'")) from exc


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_lessons() -> list[dict[str, Any]]:
    """Load active lessons from DynamoDB."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_lessons)
        now = datetime.now(UTC).isoformat()
        return client.query(
            Key("active_flag").eq("1") & Key("expires_at").gt(now),
            index_name="active_flag-expires_at-index",
        )
    except Exception as exc:
        _log.warning("load_lessons failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "active lessons")) from exc


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_portfolio_history() -> dict[str, Any]:
    """Load portfolio value history and SPY benchmark for performance comparison."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_decisions)
        # Get all decisions to build timeline
        decisions = client.query(
            Key("record_type").eq("DECISION"),
            index_name="record_type-timestamp-index",
            scan_forward=True,
        )

        if not decisions:
            return {"dates": [], "portfolio_values": [], "spy_values": [], "metrics": {}}

        from datetime import datetime as dt_cls

        try:
            import yfinance as yf
        except ImportError:
            _log.warning("yfinance not installed — portfolio history unavailable")
            return {"dates": [], "portfolio_values": [], "spy_values": [], "metrics": {}}

        timestamps = [d.get("timestamp", "") for d in decisions if d.get("timestamp")]
        if not timestamps:
            return {"dates": [], "portfolio_values": [], "spy_values": [], "metrics": {}}

        start_date = min(timestamps)[:10]  # YYYY-MM-DD

        # Fetch SPY data for benchmark
        spy = yf.download("SPY", start=start_date, progress=False)
        if spy.empty:
            return {"dates": [], "portfolio_values": [], "spy_values": [], "metrics": {}}

        # Build simplified portfolio value series from decisions
        # Use SPY dates as the x-axis, normalize both to 100 at start
        spy_closes = spy["Close"].dropna()
        dates = [d.strftime("%Y-%m-%d") for d in spy_closes.index]
        spy_values = (spy_closes / spy_closes.iloc[0] * 100).tolist()

        # Count buy/sell decisions over time as a proxy for portfolio activity
        buy_dates = [d["timestamp"][:10] for d in decisions if d.get("signal") == "BUY"]
        sell_dates = [d["timestamp"][:10] for d in decisions if d.get("signal") == "SELL"]

        # Also load current portfolio for actual return calculation
        state = load_portfolio_state(cfg.table_portfolio)
        portfolio_value = state.get("portfolio_value", 100000)

        # Calculate basic metrics
        if len(spy_values) >= 2:
            spy_return = (spy_values[-1] / spy_values[0] - 1) * 100
        else:
            spy_return = 0.0

        total_decisions = len(decisions)
        total_buys = len(buy_dates)
        total_sells = len(sell_dates)

        return {
            "dates": dates,
            "spy_values": spy_values if isinstance(spy_values, list) else [float(v) for v in spy_values],
            "buy_dates": buy_dates,
            "sell_dates": sell_dates,
            "metrics": {
                "spy_return_pct": round(spy_return, 2),
                "total_decisions": total_decisions,
                "total_buys": total_buys,
                "total_sells": total_sells,
                "start_date": start_date,
                "portfolio_value": portfolio_value,
            },
        }
    except Exception as exc:
        _log.warning("load_portfolio_history failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "portfolio history")) from exc


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_thesis_content(ticker: str) -> str | None:
    """Load the latest thesis markdown for a ticker from S3."""
    try:
        cfg = get_config()
        s3 = S3Client(bucket=cfg.s3_bucket)
        # List thesis keys for this ticker
        keys = s3.list_keys(prefix=f"theses/{ticker}/")
        if not keys:
            return None
        # Get the latest one (sorted descending)
        latest_key = sorted(keys, reverse=True)[0]
        return s3.read_markdown(latest_key)
    except Exception as exc:
        _log.warning("load_thesis_content failed", extra={"ticker": ticker, "error": str(exc)})
        return None  # Non-critical — just won't show thesis


@st.cache_data(ttl=_TTL_STATIC, show_spinner=False)
def load_config_thresholds() -> dict[str, Any]:
    """Load screening thresholds from config table."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_config)
        item = client.get_item({"config_key": "screening_thresholds"})
        return (item.get("value") or {}) if item else {}
    except Exception as exc:
        _log.warning("load_config_thresholds failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "screening thresholds")) from exc
