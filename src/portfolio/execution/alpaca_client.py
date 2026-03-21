"""
Alpaca REST API client — extracted from handler.py for independent reuse.
"""

from __future__ import annotations

from typing import Any

import requests

from shared.config import get_config
from shared.http_client import TIMEOUT, get_session


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
        """Initialize the Alpaca client, defaulting to the configured base URL."""
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
        resp = get_session().get(
            f"{self._base_url}/v2/account",
            headers=self._headers,
            timeout=TIMEOUT,
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
        # requests.post directly — intentionally NOT using get_session() to prevent
        # duplicate order submission if Alpaca returns 5xx after accepting the order.
        resp = requests.post(
            f"{self._base_url}/v2/orders",
            headers=self._headers,
            json=payload,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list[dict[str, Any]]:
        """Return current positions."""
        resp = get_session().get(
            f"{self._base_url}/v2/positions",
            headers=self._headers,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_quote(self, ticker: str) -> dict[str, Any]:
        """Return latest quote (bid/ask) from market data API."""
        resp = get_session().get(
            f"{self._data_url}/v2/stocks/{ticker.upper()}/quotes/latest",
            headers=self._headers,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
