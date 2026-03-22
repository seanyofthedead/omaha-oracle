"""
Integration tests for SEC/Yahoo/FRED ingestion — real API calls.

Mark as slow: pytest -m "not slow"
"""

from __future__ import annotations

import pytest

from shared.config import get_config

pytestmark = pytest.mark.slow


class TestSECIngestion:
    """SEC EDGAR real API calls."""

    def test_fetch_company_tickers(self):
        """Fetch SEC company_tickers.json (public, no auth)."""
        import requests

        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {"User-Agent": get_config().sec_user_agent}
        resp = requests.get(url, headers=headers, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        # Format: {0: {cik_str, ticker, title}, 1: {...}, ...}
        first = next(iter(data.values()))
        assert "ticker" in first
        assert "cik_str" in first


class TestYahooIngestion:
    """Yahoo Finance real API calls via yfinance."""

    def test_fetch_aapl_info(self):
        """Fetch AAPL fundamentals from yfinance."""
        import yfinance as yf

        t = yf.Ticker("AAPL")
        info = t.info or {}
        assert "symbol" in info or "shortName" in info or "currentPrice" in info

    def test_fetch_aapl_history(self):
        """Fetch AAPL price history."""
        import yfinance as yf

        t = yf.Ticker("AAPL")
        df = t.history(period="5d")
        assert not df.empty
        assert "Close" in df.columns


class TestFREDIngestion:
    """FRED API real calls — requires FRED_API_KEY in config."""

    def test_fetch_fred_series(self):
        """Fetch a FRED series (skip unless key available)."""
        import requests

        api_key = get_config().fred_api_key
        if not api_key:
            pytest.skip("FRED API key not set in config")
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "FEDFUNDS",
            "api_key": api_key,
            "file_type": "json",
            "limit": 5,
            "sort_order": "desc",
        }
        resp = requests.get(url, params=params, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        assert "observations" in data
