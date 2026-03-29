"""
Tests for HIGH-severity ingestion bug fixes:
  1. SEC URL placeholders substituted (no literal {start_date}/{end_date})
  2. SEC insider transactions use a global rate-limit lock (not per-thread)
  3. FRED handler caps concurrency (max_workers <= 2)
  4. Shared sec_client used by both sec_edgar and insider_transactions
"""

from __future__ import annotations

import threading
from datetime import date
from unittest.mock import MagicMock, patch


# ------------------------------------------------------------------ #
# Bug 1: SEC URL placeholders must be interpolated                     #
# ------------------------------------------------------------------ #


class TestSECUrlPlaceholders:
    """SEC sources must not send literal {start_date}/{end_date} in URLs."""

    def test_sec_fulltext_scrape_url_has_no_placeholders(self):
        """SECEdgarFullText.scrape() should format the URL with actual dates."""
        from ingestion.web_sources.sources import SECEdgarFullText

        src = SECEdgarFullText()
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {"json": {"stocks": []}}

        src.scrape(mock_client)

        call_args = mock_client.scrape_extract.call_args
        used_url = call_args.kwargs.get("url") or call_args[1].get("url") or call_args[0][0]
        assert "{start_date}" not in used_url, f"Literal placeholder in URL: {used_url}"
        assert "{end_date}" not in used_url, f"Literal placeholder in URL: {used_url}"
        # Verify actual date format appears (YYYY-MM-DD)
        import re

        assert re.search(r"\d{4}-\d{2}-\d{2}", used_url), f"No date found in URL: {used_url}"

    def test_sec_13f_scrape_url_has_no_placeholders(self):
        """SEC13FFilings.scrape() should format the URL with actual dates."""
        from ingestion.web_sources.sources import SEC13FFilings

        src = SEC13FFilings()
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {"json": {"stocks": []}}

        src.scrape(mock_client)

        call_args = mock_client.scrape_extract.call_args
        used_url = call_args.kwargs.get("url") or call_args[1].get("url") or call_args[0][0]
        assert "{start_date}" not in used_url, f"Literal placeholder in URL: {used_url}"
        assert "{end_date}" not in used_url, f"Literal placeholder in URL: {used_url}"

    def test_sec_fulltext_url_still_has_template(self):
        """The class attribute url should still contain the template (not pre-formatted)."""
        from ingestion.web_sources.sources import SECEdgarFullText

        src = SECEdgarFullText()
        assert "{start_date}" in src.url, "Template should stay in class attribute"


# ------------------------------------------------------------------ #
# Bug 2: SEC rate-limit must use a global lock                         #
# ------------------------------------------------------------------ #


class TestSECRateLimitLock:
    """Insider transactions handler must use a module-level lock for rate limiting."""

    def test_rate_limit_uses_global_lock(self):
        """_rate_limit() should acquire a module-level lock to prevent per-thread burst."""
        import ingestion.insider_transactions.handler as mod

        assert hasattr(mod, "_rate_lock"), "Module should have _rate_lock"
        assert isinstance(mod._rate_lock, type(threading.Lock())), (
            "_rate_lock should be a threading.Lock"
        )

    def test_max_workers_reduced_or_lock_present(self):
        """Either max_workers is reduced or a global lock serialises SEC requests."""
        import ingestion.insider_transactions.handler as mod

        has_lock = hasattr(mod, "_rate_lock")
        assert has_lock, "Module should have _rate_lock for thread-safe rate limiting"


# ------------------------------------------------------------------ #
# Bug 3: FRED concurrency cap                                         #
# ------------------------------------------------------------------ #


class TestFREDConcurrency:
    def test_fred_max_workers_capped(self, monkeypatch):
        """FRED handler should limit concurrency to avoid rate-limit violations."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        from concurrent.futures import ThreadPoolExecutor as RealTPE

        with (
            patch("ingestion.fred.handler.get_session") as mock_session_fn,
            patch("ingestion.fred.handler.S3Client"),
            patch(
                "ingestion.fred.handler.ThreadPoolExecutor",
                wraps=RealTPE,
            ) as mock_tpe,
        ):
            mock_session = MagicMock()
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"observations": [{"date": "2024-01-01", "value": "5.33"}]}
            mock_session.get.return_value = resp
            mock_session_fn.return_value = mock_session

            from ingestion.fred.handler import handler

            handler({}, None)

            # Check the max_workers kwarg passed to ThreadPoolExecutor
            call_kwargs = mock_tpe.call_args
            if call_kwargs:
                mw = call_kwargs.kwargs.get("max_workers") or call_kwargs[1].get(
                    "max_workers", None
                )
                if mw is None and call_kwargs[0]:
                    mw = call_kwargs[0][0]
                assert mw is not None and mw <= 2, (
                    f"FRED max_workers should be <= 2, got {mw}"
                )


# ------------------------------------------------------------------ #
# Bug 4: Shared sec_client deduplication                               #
# ------------------------------------------------------------------ #


class TestSharedSecClient:
    """_get_ticker_to_cik should be extracted into shared.sec_client."""

    def test_shared_sec_client_module_exists(self):
        """shared.sec_client should be importable."""
        import shared.sec_client  # noqa: F401

    def test_get_ticker_to_cik_returns_padded_cik(self):
        """get_ticker_to_cik should parse SEC JSON and return zero-padded CIK strings."""
        from shared.sec_client import get_ticker_to_cik

        fake_data = {
            "0": {"ticker": "AAPL", "cik_str": 320193},
            "1": {"ticker": "MSFT", "cik_str": 789019},
        }

        with patch("shared.sec_client._fetch_json", return_value=fake_data):
            result = get_ticker_to_cik("TestAgent/1.0 test@test.com")

        assert result["AAPL"] == "0000320193"
        assert result["MSFT"] == "0000789019"

    def test_sec_edgar_handler_uses_shared_client(self):
        """sec_edgar.handler should import get_ticker_to_cik from shared.sec_client."""
        import ingestion.sec_edgar.handler as mod

        # The module should no longer define its own _get_ticker_to_cik
        # Instead it should use the shared one
        from shared.sec_client import get_ticker_to_cik

        assert mod._get_ticker_to_cik is get_ticker_to_cik

    def test_insider_handler_uses_shared_client(self):
        """insider_transactions.handler should import get_ticker_to_cik from shared.sec_client."""
        import ingestion.insider_transactions.handler as mod

        from shared.sec_client import get_ticker_to_cik

        assert mod._get_ticker_to_cik is get_ticker_to_cik
