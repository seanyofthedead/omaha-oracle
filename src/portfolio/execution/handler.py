"""
Lambda handler for portfolio execution via Alpaca.

Processes pending decisions from allocation step. LIMIT ORDERS ONLY.
Paper trading by default; refuses live unless ENVIRONMENT=prod.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.converters import today_str
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .alpaca_client import AlpacaClient

_log = get_logger(__name__)

TRANCHE_THRESHOLD_USD = 10_000.0


def _check_trading_enabled(config_client: DynamoClient) -> None:
    """
    Raise RuntimeError if the kill switch has been flipped.

    Flip via:
        aws dynamodb put-item --table-name omaha-oracle-prod-config \
          --item '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"},"value":{"BOOL":false}}'
    """
    item = config_client.get_item({"pk": "config", "sk": "trading_enabled"})
    if item is not None and item.get("value") is False:
        raise RuntimeError(
            "Trading is disabled via kill switch (config.trading_enabled=false). "
            "Set value to true to resume."
        )


MIN_TRANCHES = 3
MAX_TRANCHES = 5


def _split_tranches(total_qty: float, total_usd: float) -> list[float]:
    """
    Split order into 3-5 tranches for patient accumulation when > $10K.

    Returns list of quantities per tranche (roughly equal).
    """
    if total_usd < TRANCHE_THRESHOLD_USD or total_qty <= 0:
        return [total_qty]

    n = min(MAX_TRANCHES, max(MIN_TRANCHES, int(total_usd / 3000)))
    n = max(1, n)
    qty_int = int(total_qty)
    if qty_int < n:
        return [total_qty]
    base, remainder = divmod(qty_int, n)
    tranches = [float(base)] * n
    for i in range(remainder):
        tranches[i] += 1.0
    return [t for t in tranches if t > 0]


def _alert_trade_failure(
    topic_arn: str,
    region: str,
    ticker: str,
    side: str,
    error: str,
) -> None:
    """Publish an SNS alert when a trade execution fails."""
    if not topic_arn:
        return
    from monitoring.alerts.handler import _publish  # avoid circular import at module level

    _publish(
        topic_arn=topic_arn,
        subject=f"Omaha Oracle — Trade Failure: {ticker}",
        message=(f"Failed to execute {side.upper()} order for {ticker}.\nError: {error}"),
        region=region,
    )


def _sync_portfolio_state(alpaca: AlpacaClient, portfolio_table: str) -> None:
    """Sync Alpaca positions and account balance back to DynamoDB portfolio table."""
    portfolio_client = DynamoClient(portfolio_table)
    now = datetime.now(UTC).isoformat()

    try:
        account = alpaca.get_account()
        portfolio_client.put_item(
            {
                "pk": "ACCOUNT",
                "sk": "SUMMARY",
                "portfolio_value": float(account.get("portfolio_value") or 0),
                "cash_available": float(account.get("cash") or 0),
                "last_synced": now,
            }
        )
    except Exception as exc:
        _log.error("Portfolio account sync failed", extra={"error": str(exc)})

    try:
        positions = alpaca.get_positions()
        for pos in positions:
            symbol = (pos.get("symbol") or "").upper()
            if not symbol:
                continue
            portfolio_client.put_item(
                {
                    "pk": "POSITION",
                    "sk": symbol,
                    "ticker": symbol,
                    "shares": float(pos.get("qty") or 0),
                    "market_value": float(pos.get("market_value") or 0),
                    "cost_basis": float(pos.get("cost_basis") or 0),
                    "sector": pos.get("asset_class", "equity"),
                    "last_synced": now,
                }
            )
    except Exception as exc:
        _log.error("Portfolio positions sync failed", extra={"error": str(exc)})


def _log_order_decision(
    decisions_client: DynamoClient,
    ticker: str,
    side: str,
    payload: dict[str, Any],
) -> None:
    """Log order submission to decisions table."""
    decision_id = f"ORDER#{ticker}#{side.upper()}#{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).isoformat()
    item = {
        "decision_id": decision_id,
        "timestamp": timestamp,
        "decision_type": "ORDER",
        "record_type": "DECISION",
        "ticker": ticker,
        "side": side,
        "payload": payload,
    }
    decisions_client.put_item(item)
    _log.info("order_logged", extra={"ticker": ticker, "side": side})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        decisions: list of {ticker, signal, decision_type, payload, ...}
                  from allocation step (BUY with position_size_usd, SELL)
        Or: buy_decisions, sell_decisions from allocation output

    Output:
        orders_submitted: list of order results
        orders_refused: count (e.g. non-prod live attempt)
        errors: list of error messages
    """
    cfg = get_config()
    if cfg.environment != "prod":
        # Refuse live trading; paper URL is already default
        if "api.alpaca.markets" in (cfg.alpaca_base_url or ""):
            return {
                "orders_submitted": [],
                "orders_refused": 0,
                "errors": ["Live trading refused: ENVIRONMENT must be prod for api.alpaca.markets"],
            }

    config_client = DynamoClient(cfg.table_config)
    _check_trading_enabled(config_client)

    decisions_client = DynamoClient(cfg.table_decisions)
    client = AlpacaClient()

    # Normalise input: support both {decisions} and {buy_decisions, sell_decisions}
    buy_decisions = event.get("buy_decisions", [])
    sell_decisions = event.get("sell_decisions", [])
    if "decisions" in event:
        all_dec = event["decisions"]
        for d in all_dec:
            if d.get("signal") == "BUY":
                buy_decisions.append(d)
            elif d.get("signal") == "SELL":
                sell_decisions.append(d)

    orders_submitted: list[dict[str, Any]] = []
    errors: list[str] = []

    # Process BUY decisions
    for dec in buy_decisions:
        if dec.get("signal") != "BUY":
            continue
        ticker = (dec.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        position_usd = dec.get("position_size_usd") or 0
        if position_usd <= 0:
            errors.append(f"{ticker}: no position_size_usd")
            continue

        # Idempotency: write a dedup sentinel before submitting to Alpaca.
        # If the sentinel already exists this is a Lambda retry — skip to avoid double order.
        dedup_key = f"DEDUP#{ticker}#buy#{today_str()}"
        already_submitted = not decisions_client.put_item_if_not_exists(
            {
                "pk": dedup_key,
                "sk": "DEDUP",
                "ticker": ticker,
                "side": "buy",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if already_submitted:
            _log.warning(
                "Duplicate BUY attempt skipped (idempotency)",
                extra={"ticker": ticker, "dedup_key": dedup_key},
            )
            continue

        try:
            quote = client.get_quote(ticker)
        except Exception as e:
            err = f"{ticker}: quote failed — {e}"
            _log.error("BUY quote fetch failed", extra={"ticker": ticker, "error": str(e)})
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "buy", str(e))
            continue

        quote_obj = quote.get("quote") or {}
        ask = float(quote_obj.get("ap", 0) or 0)
        if ask <= 0:
            err = f"{ticker}: no ask price"
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "buy", err)
            continue

        total_qty = position_usd / ask
        tranches = _split_tranches(total_qty, position_usd)

        for i, qty in enumerate(tranches):
            if qty < 0.0001:
                continue
            try:
                order = client.submit_order(
                    ticker=ticker,
                    qty=qty,
                    side="buy",
                    order_type="limit",
                    limit_price=ask,
                    time_in_force="day",
                )
                orders_submitted.append(order)
                _log_order_decision(
                    decisions_client,
                    ticker,
                    "buy",
                    {
                        "order_id": order.get("id"),
                        "qty": qty,
                        "limit_price": ask,
                        "tranche": i + 1,
                        "total_tranches": len(tranches),
                    },
                )
            except Exception as e:
                err = f"{ticker} tranche {i + 1}: {e}"
                _log.error(
                    "BUY order submit failed",
                    extra={"ticker": ticker, "tranche": i + 1, "error": str(e)},
                )
                errors.append(err)
                _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "buy", str(e))

    # Process SELL decisions
    for dec in sell_decisions:
        if dec.get("signal") != "SELL":
            continue
        ticker = (dec.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        # Idempotency guard for SELL too
        dedup_key = f"DEDUP#{ticker}#sell#{today_str()}"
        already_submitted = not decisions_client.put_item_if_not_exists(
            {
                "pk": dedup_key,
                "sk": "DEDUP",
                "ticker": ticker,
                "side": "sell",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if already_submitted:
            _log.warning(
                "Duplicate SELL attempt skipped (idempotency)",
                extra={"ticker": ticker, "dedup_key": dedup_key},
            )
            continue

        try:
            positions = client.get_positions()
            pos = next((p for p in positions if (p.get("symbol") or "").upper() == ticker), None)
        except Exception as e:
            err = f"{ticker}: get_positions failed — {e}"
            _log.error("SELL positions fetch failed", extra={"ticker": ticker, "error": str(e)})
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "sell", str(e))
            continue

        if not pos:
            errors.append(f"{ticker}: no position to sell")
            continue

        qty = float(pos.get("qty", 0) or 0)
        qty = abs(qty)
        if qty <= 0:
            errors.append(f"{ticker}: invalid position qty")
            continue

        try:
            quote = client.get_quote(ticker)
        except Exception as e:
            err = f"{ticker}: quote failed — {e}"
            _log.error("SELL quote fetch failed", extra={"ticker": ticker, "error": str(e)})
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "sell", str(e))
            continue

        quote_obj = quote.get("quote") or {}
        bid = float(quote_obj.get("bp", 0) or 0)
        if bid <= 0:
            err = f"{ticker}: no bid price"
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "sell", err)
            continue

        try:
            order = client.submit_order(
                ticker=ticker,
                qty=qty,
                side="sell",
                order_type="limit",
                limit_price=bid,
                time_in_force="day",
            )
            orders_submitted.append(order)
            _log_order_decision(
                decisions_client,
                ticker,
                "sell",
                {
                    "order_id": order.get("id"),
                    "qty": qty,
                    "limit_price": bid,
                },
            )
        except Exception as e:
            err = f"{ticker} sell: {e}"
            _log.error("SELL order submit failed", extra={"ticker": ticker, "error": str(e)})
            errors.append(err)
            _alert_trade_failure(cfg.sns_topic_arn, cfg.aws_region, ticker, "sell", str(e))

    # Always sync portfolio state from Alpaca so the next allocation run sees fresh data
    _sync_portfolio_state(client, cfg.table_portfolio)

    total_decisions = len(buy_decisions) + len(sell_decisions)
    if errors and not orders_submitted and total_decisions > 0:
        raise RuntimeError(
            f"Execution failed entirely — 0 orders placed, {len(errors)} errors: {errors}"
        )

    if errors and orders_submitted:
        _log.error(
            "Partial execution failure",
            extra={"orders_placed": len(orders_submitted), "errors": errors},
        )

    return {
        "orders_submitted": orders_submitted,
        "orders_refused": 0,
        "errors": errors,
    }
