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
from typing import Any

import streamlit as st
from boto3.dynamodb.conditions import Key
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from shared.analysis_client import merge_latest_analysis, merge_latest_analysis_with_stages
from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

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
    """Load recent decisions (buy/sell signals) sorted by timestamp.

    Tries the ``record_type-timestamp-index`` GSI first.  If the GSI does not
    exist yet (table created before ``cdk deploy`` added it), falls back to a
    full-table scan filtered client-side.
    """
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_decisions)
        try:
            return client.query(
                Key("record_type").eq("DECISION"),
                index_name="record_type-timestamp-index",
                scan_forward=False,
                limit=limit,
            )
        except ClientError as gsi_exc:
            code = gsi_exc.response.get("Error", {}).get("Code", "")
            if code != "ValidationException":
                raise
            # GSI missing — fall back to scan
            _log.warning(
                "GSI record_type-timestamp-index not found on decisions table, "
                "falling back to scan. Run 'cdk deploy' to create the index.",
            )
            all_items = client.scan_all()
            decisions = [item for item in all_items if item.get("record_type") == "DECISION"]
            decisions.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
            return decisions[:limit]
    except DataLoadError:
        raise
    except Exception as exc:
        _log.warning("load_decisions failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "buy/sell decisions")) from exc


@st.cache_data(ttl=_TTL_MARKET, show_spinner=False)
def load_predictions() -> list[dict[str, Any]]:
    """Load predictions from decisions table, flattened from decision payloads.

    Returns a flat list of prediction dicts enriched with ticker and decision metadata.
    """
    try:
        decisions = load_decisions(limit=200)
    except DataLoadError:
        raise
    except Exception as exc:
        _log.warning("load_predictions failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "prediction data")) from exc

    predictions: list[dict[str, Any]] = []
    for decision in decisions:
        payload = decision.get("payload") or {}
        preds = payload.get("predictions")
        if not preds or not isinstance(preds, list):
            continue

        ticker = (decision.get("ticker") or "").strip().upper()
        decision_ts = decision.get("timestamp", "")

        for pred in preds:
            if not isinstance(pred, dict):
                continue
            predictions.append(
                {
                    **pred,
                    "ticker": ticker,
                    "decision_timestamp": decision_ts,
                    "decision_id": decision.get("decision_id", ""),
                }
            )

    # Sort: pending first (by deadline ascending), then evaluated (by deadline descending)
    def sort_key(p: dict) -> tuple:
        is_pending = 0 if p.get("status") == "pending" else 1
        return (is_pending, p.get("deadline", ""))

    predictions.sort(key=sort_key)
    return predictions


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_all_pipeline_candidates(analysis_date: str | None = None) -> list[dict[str, Any]]:
    """Load ALL screened candidates from the analysis table with stage-level pass/fail.

    Unlike ``load_watchlist_analysis`` which only returns watchlist tickers,
    this scans the entire analysis table to surface every company that was
    ever screened — including those that failed early stages.

    Parameters
    ----------
    analysis_date:
        Optional date prefix (``YYYY-MM-DD``) to filter results to a single
        pipeline run.  When ``None``, returns the latest run per ticker.
    """
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_analysis)

        if analysis_date:
            from boto3.dynamodb.conditions import Attr

            all_items = client.scan_all(
                filter_expression=Attr("analysis_date").begins_with(analysis_date),
            )
        else:
            all_items = client.scan_all()
    except Exception as exc:
        _log.warning("load_all_pipeline_candidates failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "pipeline candidates")) from exc

    if not all_items:
        return []

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in all_items:
        t = item.get("ticker", "").strip().upper()
        if t:
            by_ticker.setdefault(t, []).append(item)

    candidates = []
    for ticker, items in sorted(by_ticker.items()):
        merged = merge_latest_analysis_with_stages(items, ticker)
        if merged:
            candidates.append(merged)
    return candidates


@st.cache_data(ttl=_TTL_ANALYSIS, show_spinner=False)
def load_pipeline_run_dates() -> list[str]:
    """Return distinct analysis run dates, newest first."""
    try:
        cfg = get_config()
        client = DynamoClient(cfg.table_analysis)
        all_items = client.scan_all()
    except Exception as exc:
        _log.warning("load_pipeline_run_dates failed", extra={"error": str(exc)})
        raise DataLoadError(_friendly_aws_message(exc, "pipeline run dates")) from exc

    dates: set[str] = set()
    for item in all_items:
        sk = item.get("analysis_date", "")
        date_part = sk.split("#")[0] if "#" in sk else sk
        if date_part:
            dates.add(date_part)
    return sorted(dates, reverse=True)
