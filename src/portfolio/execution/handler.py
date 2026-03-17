"""
Lambda handler for portfolio execution via Alpaca.

Processes pending decisions from allocation step. LIMIT ORDERS ONLY.
Paper trading by default; refuses live unless ENVIRONMENT=prod.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import requests

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

TRANCHE_THRESHOLD_USD = 10_000.0
MIN_TRANCHES = 3
MAX_TRANCHES = 5


def _data_url_from_base(base_url: str) -> str:
    """Derive market data API URL from trading API base URL."""
    if "paper-api" in base_url:
        return "https://data.sandbox.alpaca.markets"
    return "https://data.alpaca.markets"


class AlpacaClient:
    """
    Alpaca REST API client using requests (no alpaca-py SDK).

    Paper trading by default via base_url from config.
    """

    def __init__(self, base_url: str | None = None) -> None:
        cfg = get_config()
        self._base_url = (base_url or cfg.alpaca_base_url).rstrip("/")
        self._data_url = _data_url_from_base(self._base_url).rstrip("/")
        self._api_key, self._secret_key = cfg.get_alpaca_keys()
        self._headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Content-Type": "application/json",
        }

    def get_account(self) -> dict[str, Any]:
        """Return account info and buying power."""
        resp = requests.get(
            f"{self._base_url}/v2/account",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def submit_order(
        self,
        ticker: str,
        qty: float,
        side: str,
        order_type: str = "limit",
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """
        Submit a limit order. LIMIT ORDERS ONLY.

        Parameters
        ----------
        ticker : str
            Symbol (e.g. AAPL).
        qty : float
            Number of shares.
        side : str
            "buy" or "sell".
        order_type : str
            Must be "limit".
        limit_price : float
            Required for limit orders.
        time_in_force : str
            "day", "gtc", etc. Default "day".
        """
        if order_type != "limit" or limit_price is None:
            raise ValueError("Only limit orders supported; limit_price required")

        qty_str = str(int(qty)) if qty == int(qty) else f"{qty:.4f}"
        payload = {
            "symbol": ticker.upper(),
            "qty": qty_str,
            "side": side.lower(),
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": time_in_force,
        }
        resp = requests.post(
            f"{self._base_url}/v2/orders",
            headers=self._headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list[dict[str, Any]]:
        """Return current positions."""
        resp = requests.get(
            f"{self._base_url}/v2/positions",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_quote(self, ticker: str) -> dict[str, Any]:
        """Return latest quote (bid/ask) from market data API."""
        resp = requests.get(
            f"{self._data_url}/v2/stocks/{ticker.upper()}/quotes/latest",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


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
                "errors": [
                    "Live trading refused: ENVIRONMENT must be prod for api.alpaca.markets"
                ],
            }

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

        try:
            quote = client.get_quote(ticker)
        except Exception as e:
            errors.append(f"{ticker}: quote failed — {e}")
            continue

        quote_obj = quote.get("quote") or {}
        ask = float(quote_obj.get("ap", 0) or 0)
        if ask <= 0:
            errors.append(f"{ticker}: no ask price")
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
                errors.append(f"{ticker} tranche {i+1}: {e}")

    # Process SELL decisions
    for dec in sell_decisions:
        if dec.get("signal") != "SELL":
            continue
        ticker = (dec.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        try:
            positions = client.get_positions()
            pos = next((p for p in positions if (p.get("symbol") or "").upper() == ticker), None)
        except Exception as e:
            errors.append(f"{ticker}: get_positions failed — {e}")
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
            errors.append(f"{ticker}: quote failed — {e}")
            continue

        quote_obj = quote.get("quote") or {}
        bid = float(quote_obj.get("bp", 0) or 0)
        if bid <= 0:
            errors.append(f"{ticker}: no bid price")
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
            errors.append(f"{ticker} sell: {e}")

    return {
        "orders_submitted": orders_submitted,
        "orders_refused": 0,
        "errors": errors,
    }
