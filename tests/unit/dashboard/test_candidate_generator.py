"""Tests for candidate generation, smart screening, and pre-screening."""

from __future__ import annotations

from unittest.mock import patch

from dashboard.candidate_generator import (
    SmartCandidateGenerator,
    _score_de,
    _score_fcf_yield,
    _score_pb,
    _score_pe,
    _score_roe,
    ingest_ticker_data,
    load_sec_universe,
    pre_screen_ticker,
    rank_candidates,
    score_candidate,
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
# B3: Individual scoring functions
# ---------------------------------------------------------------------------


class TestScoringFunctions:
    def test_score_pe_at_threshold(self):
        assert _score_pe(15.0) == 0.0

    def test_score_pe_at_zero(self):
        assert _score_pe(0.0) == 0.0  # pe <= 0 returns 0

    def test_score_pe_midpoint(self):
        score = _score_pe(7.5)
        assert 0.49 < score < 0.51  # should be ~0.5

    def test_score_pe_low(self):
        assert _score_pe(3.0) > _score_pe(12.0)

    def test_score_pe_negative(self):
        assert _score_pe(-5.0) == 0.0

    def test_score_pb_at_threshold(self):
        assert _score_pb(1.5) == 0.0

    def test_score_pb_midpoint(self):
        score = _score_pb(0.75)
        assert 0.49 < score < 0.51

    def test_score_de_at_zero(self):
        assert _score_de(0.0) == 1.0

    def test_score_de_at_threshold(self):
        assert _score_de(0.5) == 0.0

    def test_score_de_negative(self):
        assert _score_de(-0.1) == 0.0

    def test_score_roe_at_threshold(self):
        assert _score_roe(12.0) == 0.0

    def test_score_roe_at_ceiling(self):
        assert _score_roe(30.0) == 1.0

    def test_score_roe_above_ceiling(self):
        assert _score_roe(50.0) == 1.0  # capped at 1

    def test_score_roe_midpoint(self):
        score = _score_roe(21.0)
        assert 0.49 < score < 0.51

    def test_score_fcf_yield_zero_mcap(self):
        assert _score_fcf_yield(1_000_000, 0) == 0.0

    def test_score_fcf_yield_negative_fcf(self):
        assert _score_fcf_yield(-100, 1_000_000) == 0.0

    def test_score_fcf_yield_high(self):
        # 10% yield = max score
        assert _score_fcf_yield(100_000_000, 1_000_000_000) == 1.0

    def test_score_fcf_yield_moderate(self):
        # 5% yield = ~0.5
        score = _score_fcf_yield(50_000_000, 1_000_000_000)
        assert 0.49 < score < 0.51


# ---------------------------------------------------------------------------
# B4: score_candidate
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def test_score_perfect_candidate(self):
        candidate = {
            "trailingPE": 5.0,
            "priceToBook": 0.5,
            "debtToEquity": 10.0,  # 10% in percentage form
            "returnOnEquity": 0.25,  # 25% as fraction
            "leveredFreeCashflow": 100_000_000,
            "marketCap": 1_000_000_000,
        }
        score = score_candidate(candidate)
        assert score > 0.5  # Should be a high score

    def test_score_borderline_candidate(self):
        candidate = {
            "trailingPE": 14.5,
            "priceToBook": 1.4,
            "debtToEquity": 48.0,  # 48% in percentage form
            "returnOnEquity": 0.13,  # 13%
            "leveredFreeCashflow": 1_000,
            "marketCap": 1_000_000_000,
        }
        score = score_candidate(candidate)
        assert 0 < score < 0.2  # Should be a low score

    def test_score_missing_fields(self):
        candidate = {"trailingPE": 8.0}
        score = score_candidate(candidate)
        # Should still produce a score from available fields
        assert score > 0

    def test_score_empty_candidate(self):
        score = score_candidate({})
        assert score == 0.0

    def test_score_roe_percentage_form(self):
        """ROE reported as 25 (percent) vs 0.25 (fraction) should give same score."""
        c_pct = {"returnOnEquity": 25.0}
        c_frac = {"returnOnEquity": 0.25}
        assert abs(score_candidate(c_pct) - score_candidate(c_frac)) < 0.01

    def test_better_candidate_scores_higher(self):
        good = {
            "trailingPE": 5.0,
            "priceToBook": 0.5,
            "debtToEquity": 5.0,
            "returnOnEquity": 0.28,
            "leveredFreeCashflow": 200_000_000,
            "marketCap": 1_000_000_000,
        }
        bad = {
            "trailingPE": 14.0,
            "priceToBook": 1.4,
            "debtToEquity": 45.0,
            "returnOnEquity": 0.13,
            "leveredFreeCashflow": 5_000_000,
            "marketCap": 5_000_000_000,
        }
        assert score_candidate(good) > score_candidate(bad)


# ---------------------------------------------------------------------------
# B5: rank_candidates
# ---------------------------------------------------------------------------


class TestRankCandidates:
    def test_rank_sorts_descending(self):
        candidates = [
            {"symbol": "BAD", "trailingPE": 14.0, "priceToBook": 1.4},
            {"symbol": "GOOD", "trailingPE": 5.0, "priceToBook": 0.5},
            {"symbol": "MED", "trailingPE": 10.0, "priceToBook": 1.0},
        ]
        ranked = rank_candidates(candidates)
        symbols = [c["symbol"] for c in ranked]
        assert symbols[0] == "GOOD"
        assert symbols[-1] == "BAD"

    def test_rank_adds_composite_score(self):
        candidates = [{"symbol": "A", "trailingPE": 8.0}]
        ranked = rank_candidates(candidates)
        assert "_composite_score" in ranked[0]

    def test_rank_empty_list(self):
        assert rank_candidates([]) == []


# ---------------------------------------------------------------------------
# B6: SmartCandidateGenerator
# ---------------------------------------------------------------------------


class TestSmartCandidateGenerator:
    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_returns_ranked_tickers(self, mock_universe, mock_fetch):
        mock_universe.return_value = {"AAPL": "CIK1", "MSFT": "CIK2", "GOOG": "CIK3"}
        mock_fetch.return_value = [
            {"symbol": "GOOG", "trailingPE": 12.0, "priceToBook": 1.2},
            {"symbol": "AAPL", "trailingPE": 5.0, "priceToBook": 0.8},
            {"symbol": "MSFT", "trailingPE": 8.0, "priceToBook": 1.0},
        ]
        gen = SmartCandidateGenerator()
        batch = gen.generate_batch(3)
        assert len(batch) == 3
        # AAPL has lowest P/E + P/B, should be first
        assert batch[0] == "AAPL"

    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_skips_evaluated(self, mock_universe, mock_fetch):
        mock_universe.return_value = {"A": "1", "B": "2", "C": "3"}
        mock_fetch.return_value = [
            {"symbol": "A", "trailingPE": 5.0},
            {"symbol": "B", "trailingPE": 8.0},
            {"symbol": "C", "trailingPE": 10.0},
        ]
        gen = SmartCandidateGenerator(evaluated={"A"})
        batch = gen.generate_batch(10)
        assert "A" not in batch
        assert len(batch) == 2

    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_generate_batch_exhaustion(self, mock_universe, mock_fetch):
        mock_universe.return_value = {}
        mock_fetch.return_value = [
            {"symbol": "A", "trailingPE": 5.0},
        ]
        gen = SmartCandidateGenerator()
        batch1 = gen.generate_batch(1)
        assert len(batch1) == 1
        batch2 = gen.generate_batch(1)
        assert batch2 == []

    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_screener_count_set_after_init(self, mock_universe, mock_fetch):
        mock_universe.return_value = {}
        mock_fetch.return_value = [
            {"symbol": "A", "trailingPE": 5.0},
            {"symbol": "B", "trailingPE": 8.0},
        ]
        gen = SmartCandidateGenerator()
        gen.generate_batch(0)  # trigger init
        assert gen.screener_count == 2

    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    @patch("dashboard.candidate_generator.yf")
    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_fallback_on_empty_screener(self, mock_universe, mock_yf, mock_fetch):
        mock_universe.return_value = {}
        mock_fetch.return_value = []  # Tier 1 returns nothing
        mock_yf.screen.return_value = {"quotes": [{"symbol": "FALLBACK", "trailingPE": 10.0}]}
        gen = SmartCandidateGenerator()
        batch = gen.generate_batch(10)
        assert "FALLBACK" in batch

    @patch("dashboard.candidate_generator.load_sec_universe")
    def test_get_cik(self, mock_universe):
        mock_universe.return_value = {"AAPL": "CIK123"}
        gen = SmartCandidateGenerator()
        assert gen.get_cik("AAPL") == "CIK123"
        assert gen.get_cik("UNKNOWN") is None


# ---------------------------------------------------------------------------
# B7: ingest_ticker_data
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
