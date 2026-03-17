"""
Centralized alert handler — sends SNS notifications for operational events.

Alert types: buy_signal, trade_executed, thesis_break, error,
             weekly_digest, postmortem_complete
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import boto3

from shared.config import get_config
from shared.logger import get_logger

_log = get_logger(__name__)

SUBJECT_PREFIX = "Omaha Oracle"


def _format_buy_signal(data: dict[str, Any]) -> tuple[str, str]:
    """Format buy/sell signal alert."""
    ticker = data.get("ticker", "?")
    signal = data.get("signal", "BUY")
    mos = data.get("margin_of_safety")
    thesis = data.get("thesis_summary", "")
    subject = f"{SUBJECT_PREFIX} — {signal} signal: {ticker}"
    msg = f"{signal} signal for {ticker}\n"
    if mos is not None:
        msg += f"Margin of safety: {float(mos):.1%}\n"
    if thesis:
        msg += f"Thesis: {thesis[:300]}{'...' if len(thesis) > 300 else ''}\n"
    msg += f"Full data: {json.dumps(data, default=str)[:500]}"
    return subject, msg


def _format_trade_executed(data: dict[str, Any]) -> tuple[str, str]:
    """Format executed trade alert."""
    ticker = data.get("ticker", "?")
    side = data.get("side", "buy")
    price = data.get("price") or data.get("limit_price")
    qty = data.get("qty")
    tranche = data.get("tranche")
    total_tranches = data.get("total_tranches")
    subject = f"{SUBJECT_PREFIX} — Trade executed: {side.upper()} {ticker}"
    msg = f"{side.upper()} {ticker}: {qty} shares @ ${price}\n"
    if tranche is not None and total_tranches is not None:
        msg += f"Tranche {tranche}/{total_tranches}\n"
    msg += f"Full data: {json.dumps(data, default=str)[:500]}"
    return subject, msg


def _format_thesis_break(data: dict[str, Any]) -> tuple[str, str]:
    """Format thesis-breaking event alert."""
    ticker = data.get("ticker", "?")
    reason = data.get("reason", "Unknown")
    moat_drop = data.get("moat_score_drop")
    accounting = data.get("accounting_irregularity")
    subject = f"{SUBJECT_PREFIX} — Thesis break: {ticker}"
    msg = f"Thesis-breaking event for {ticker}\nReason: {reason}\n"
    if moat_drop is not None:
        msg += f"Moat score drop: {moat_drop}\n"
    if accounting:
        msg += f"Accounting irregularity: {accounting}\n"
    msg += f"Full data: {json.dumps(data, default=str)[:500]}"
    return subject, msg


def _format_error(data: dict[str, Any]) -> tuple[str, str]:
    """Format system error alert."""
    source = data.get("source", "Lambda")
    error = data.get("error", "Unknown error")
    count = data.get("failure_count")
    subject = f"{SUBJECT_PREFIX} — System error: {source}"
    msg = f"Error in {source}: {error}\n"
    if count is not None:
        msg += f"Failure count (last hour): {count}\n"
    msg += f"Full data: {json.dumps(data, default=str)[:500]}"
    return subject, msg


def _format_weekly_digest(data: dict[str, Any]) -> tuple[str, str]:
    """Format weekly digest alert."""
    subject = f"{SUBJECT_PREFIX} — Weekly digest"
    portfolio = data.get("portfolio_summary", {})
    watchlist = data.get("watchlist_updates", [])
    msg = "Weekly portfolio digest\n\n"
    msg += f"Portfolio: {json.dumps(portfolio, indent=2, default=str)[:400]}\n\n"
    if watchlist:
        msg += f"Watchlist updates: {json.dumps(watchlist, default=str)[:300]}\n"
    msg += f"\nFull data: {json.dumps(data, default=str)[:300]}"
    return subject, msg


def _format_postmortem_complete(data: dict[str, Any]) -> tuple[str, str]:
    """Format post-mortem results alert."""
    quarter = data.get("quarter", "?")
    lessons = data.get("lessons_extracted", 0)
    auto_applied = data.get("auto_applied", [])
    flagged = data.get("flagged_for_review", [])
    subject = f"{SUBJECT_PREFIX} — Post-mortem complete: {quarter}"
    msg = f"Post-mortem for {quarter} complete\n"
    msg += f"Lessons extracted: {lessons}\n"
    msg += f"Auto-applied adjustments: {len(auto_applied)}\n"
    if auto_applied:
        for a in auto_applied[:5]:
            msg += f"  - {a.get('parameter')}: {a.get('old_value')} -> {a.get('new_value')}\n"
    msg += f"Flagged for review: {len(flagged)}\n"
    if flagged:
        for f in flagged[:5]:
            msg += f"  - {f.get('parameter')}: {f.get('proposed_value')} ({f.get('severity')})\n"
    msg += f"\nFull data: {json.dumps(data, default=str)[:500]}"
    return subject, msg


_FORMATTERS: dict[str, Callable[[dict[str, Any]], tuple[str, str]]] = {
    "buy_signal": _format_buy_signal,
    "trade_executed": _format_trade_executed,
    "thesis_break": _format_thesis_break,
    "error": _format_error,
    "weekly_digest": _format_weekly_digest,
    "postmortem_complete": _format_postmortem_complete,
}


def _publish(topic_arn: str, subject: str, message: str, region: str) -> bool:
    """Publish to SNS. Returns True on success."""
    try:
        client = boto3.client("sns", region_name=region)
        client.publish(TopicArn=topic_arn, Subject=subject, Message=message)
        _log.info("SNS alert sent", extra={"subject": subject[:50]})
        return True
    except Exception as exc:
        _log.error("SNS publish failed", extra={"error": str(exc), "subject": subject[:50]})
        return False


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        alert_type: buy_signal | trade_executed | thesis_break | error |
                    weekly_digest | postmortem_complete
        data: dict with alert-specific fields

    Output:
        sent: bool
        subject: str
        error: str | None
    """
    cfg = get_config()
    topic_arn = cfg.sns_topic_arn
    alert_type = (event.get("alert_type") or "").strip().lower()
    data = event.get("data") or {}

    if not topic_arn:
        _log.warning("No SNS_TOPIC_ARN — alert not sent")
        return {"sent": False, "subject": "", "error": "No SNS_TOPIC_ARN configured"}

    formatter = _FORMATTERS.get(alert_type)
    if not formatter:
        _log.warning("Unknown alert_type", extra={"alert_type": alert_type})
        subject = f"{SUBJECT_PREFIX} — Unknown alert: {alert_type}"
        msg = json.dumps(data, indent=2, default=str)[:1000]
    else:
        subject, msg = formatter(data)

    sent = _publish(topic_arn, subject, msg, cfg.aws_region)
    return {"sent": sent, "subject": subject, "error": None if sent else "SNS publish failed"}
