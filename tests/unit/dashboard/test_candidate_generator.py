"""Tests for candidate generation and pre-screening."""

from __future__ import annotations

from unittest.mock import patch

from dashboard.candidate_generator import (
    CandidateGenerator,
    ingest_ticker_data,
    load_sec_universe,
    pre_screen_ticker,
)

# ---------------------------------------------------------------------------
# B1: load_sec_universe
# ---------------------------------------------------------------------------


class TestLoadSecUniverse:
    @patch("dashboard.candidate_generator._get_ticker_to_cik")
    def test_load_sec_universe_returns_dict(self, mock_get):
        mock_get.return_value = {"AAPL": "0000000320", "MSFT": "0000789019"}
        result = load_sec_universe("test-agent")
        assert result == {"AAPL": "0000000320", "MSFT": "0000789019"}
        mock_get.assert_called_once()

    @patch("dashboard.candidate_generator._get_ticker_to_cik")
    def test_load_sec_universe_caches(self, mock_get):
        mock_get.return_value = {"AAPL": "0000000320"}
        # Reset module-level cache
        import dashboard.candidate_generator as cg

        cg._universe_cache = None
        result1 = load_sec_universe("test-agent")
        result2 = load_sec_universe("test-agent")
        assert result1 == result2
        mock_get.assert_called_once()
        # Clean up
        cg._universe_cache = None


# ---------------------------------------------------------------------------
# B2: pre_screen_ticker
# ---------------------------------------------------------------------------


class TestPreScreenTicker:
    @patch("dashboard.candidate_generator._fetch_prices")
    def test_pre_screen_passes_quality_company(self, mock_fetch):
        mock_fetch.return_value = {
            "marketCap": 3_000_000_000_000,
            "trailingPE": 25.0,
            "sector": "Technology",
            "industry": "Consumer Electronics",
        }
        passed, info = pre_screen_ticker("AAPL")
        assert passed is True
        assert info["marketCap"] == 3_000_000_000_000

    @patch("dashboard.candidate_generator._fetch_prices")
    def test_pre_screen_rejects_low_market_cap(self, mock_fetch):
        mock_fetch.return_value = {
            "marketCap": 50_000_000,
            "trailingPE": 10.0,
            "sector": "Technology",
        }
        passed, info = pre_screen_ticker("TINY")
        assert passed is False

    @patch("dashboard.candidate_generator._fetch_prices")
    def test_pre_screen_rejects_missing_pe(self, mock_fetch):
        mock_fetch.return_value = {
            "marketCap": 5_000_000_000,
            "trailingPE": None,
            "sector": "Technology",
        }
        passed, info = pre_screen_ticker("NOPE")
        assert passed is False

    @patch("dashboard.candidate_generator._fetch_prices")
    def test_pre_screen_handles_yfinance_error(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network error")
        passed, info = pre_screen_ticker("ERR")
        assert passed is False
        assert info == {}

    @patch("dashboard.candidate_generator._fetch_prices")
    def test_pre_screen_rejects_etf(self, mock_fetch):
        mock_fetch.return_value = {
            "marketCap": 5_000_000_000,
            "trailingPE": 15.0,
            # no 'sector' field — ETF/fund
        }
        passed, info = pre_screen_ticker("SPY")
        assert passed is False


# ---------------------------------------------------------------------------
# B3: CandidateGenerator
# ---------------------------------------------------------------------------


class TestCandidateGenerator:
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_returns_requested_size(self, mock_universe):
        mock_universe.return_value = {f"T{i}": f"CIK{i}" for i in range(100)}
        gen = CandidateGenerator(seed=42)
        batch = gen.generate_batch(10)
        assert len(batch) == 10

    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_skips_evaluated(self, mock_universe):
        tickers = {f"T{i}": f"CIK{i}" for i in range(20)}
        mock_universe.return_value = tickers
        evaluated = {f"T{i}" for i in range(5)}
        gen = CandidateGenerator(evaluated=evaluated, seed=42)
        batch = gen.generate_batch(20)
        for t in evaluated:
            assert t not in batch

    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_exhaustion(self, mock_universe):
        mock_universe.return_value = {"T0": "CIK0", "T1": "CIK1", "T2": "CIK2"}
        gen = CandidateGenerator(evaluated={"T0", "T1", "T2"}, seed=42)
        batch = gen.generate_batch(10)
        assert batch == []

    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_deterministic_with_seed(self, mock_universe):
        mock_universe.return_value = {f"T{i}": f"CIK{i}" for i in range(50)}
        gen1 = CandidateGenerator(seed=123)
        gen2 = CandidateGenerator(seed=123)
        assert gen1.generate_batch(10) == gen2.generate_batch(10)


# ---------------------------------------------------------------------------
# B4: ingest_ticker_data
# ---------------------------------------------------------------------------


class TestIngestTickerData:
    @patch("dashboard.candidate_generator._sec_process_ticker")
    @patch("dashboard.candidate_generator._yf_process_ticker")
    def test_ingest_calls_yahoo_and_sec(self, mock_yf, mock_sec):
        mock_yf.return_value = True
        mock_sec.return_value = 5
        result = ingest_ticker_data("AAPL", "0000000320")
        assert result is True
        mock_yf.assert_called_once()
        mock_sec.assert_called_once()

    @patch("dashboard.candidate_generator._sec_process_ticker")
    @patch("dashboard.candidate_generator._yf_process_ticker")
    def test_ingest_returns_false_on_yahoo_failure(self, mock_yf, mock_sec):
        mock_yf.side_effect = Exception("Yahoo down")
        result = ingest_ticker_data("AAPL", "0000000320")
        assert result is False

    @patch("dashboard.candidate_generator._sec_process_ticker")
    @patch("dashboard.candidate_generator._yf_process_ticker")
    def test_ingest_continues_if_sec_fails(self, mock_yf, mock_sec):
        mock_yf.return_value = True
        mock_sec.side_effect = Exception("SEC down")
        result = ingest_ticker_data("AAPL", "0000000320")
        assert result is True
