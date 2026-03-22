"""Tests for the search runner (pipeline execution + search loop)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from dashboard.search_config import SearchConfig
from dashboard.search_runner import (
    SearchProgress,
    SearchResult,
    _run_pipeline_stages,
    _run_thesis_for_qualifiers,
    run_search,
)

# ---------------------------------------------------------------------------
# C1: SearchResult dataclass
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_search_result_from_pipeline_output(self):
        sr = SearchResult(
            ticker="AAPL",
            company_name="Apple Inc.",
            moat_score=8,
            management_score=7,
            margin_of_safety=0.35,
            intrinsic_value=180.0,
            current_price=120.0,
            passed_all_gates=True,
            gate_details={"moat": True, "management": True, "mos": True},
            gates_passed_count=3,
            error=None,
            raw_result={"ticker": "AAPL"},
        )
        assert sr.ticker == "AAPL"
        assert sr.moat_score == 8
        assert sr.passed_all_gates is True
        assert sr.gates_passed_count == 3
        assert sr.error is None


# ---------------------------------------------------------------------------
# C2: SearchProgress dataclass
# ---------------------------------------------------------------------------


class TestSearchProgress:
    def test_search_progress_defaults(self):
        sp = SearchProgress(
            evaluated_count=5,
            match_count=2,
            elapsed_seconds=30.0,
            time_limit_seconds=900,
            current_ticker="AAPL",
            current_action="Running pipeline",
            results=[],
            is_complete=False,
            was_cancelled=False,
            candidates_exhausted=False,
        )
        assert sp.evaluated_count == 5
        assert sp.match_count == 2


# ---------------------------------------------------------------------------
# C3: _run_pipeline_stages
# ---------------------------------------------------------------------------


class TestRunPipelineStages:
    def _make_handler(self, extra: dict):
        def _handler(event, context):
            result = dict(event)
            result.update(extra)
            return result

        return MagicMock(side_effect=_handler)

    @patch("dashboard.search_runner.screen_company")
    def test_pipeline_skips_analysis_if_quant_fails(self, mock_screen):
        mock_screen.return_value = ({"ticker": "BAD", "pass": False}, False)

        with patch("dashboard.search_runner.moat_handler") as mock_moat:
            result = _run_pipeline_stages("BAD", {"ticker": "BAD"}, MagicMock())
            mock_moat.assert_not_called()
            assert result.get("quant_passed") is False

    @patch("dashboard.search_runner.screen_company")
    @patch("dashboard.search_runner.iv_handler")
    @patch("dashboard.search_runner.mgmt_handler")
    @patch("dashboard.search_runner.moat_handler")
    def test_pipeline_runs_stages_2_4(self, mock_moat, mock_mgmt, mock_iv, mock_screen):
        mock_screen.return_value = ({"ticker": "AAPL", "pass": True}, True)
        mock_moat.side_effect = lambda e, c: {**e, "moat_score": 8}
        mock_mgmt.side_effect = lambda e, c: {**e, "management_score": 7}
        mock_iv.side_effect = lambda e, c: {
            **e, "intrinsic_value_per_share": 180.0, "margin_of_safety": 0.35,
        }

        with patch("dashboard.search_runner.thesis_handler") as mock_thesis:
            result = _run_pipeline_stages("AAPL", {"ticker": "AAPL"}, MagicMock())
            mock_moat.assert_called_once()
            mock_mgmt.assert_called_once()
            mock_iv.assert_called_once()
            mock_thesis.assert_not_called()
            assert result["moat_score"] == 8

    @patch("dashboard.search_runner.screen_company")
    @patch("dashboard.search_runner.moat_handler")
    def test_pipeline_returns_error_on_handler_failure(self, mock_moat, mock_screen):
        mock_screen.return_value = ({"ticker": "ERR", "pass": True}, True)
        mock_moat.side_effect = Exception("LLM timeout")

        result = _run_pipeline_stages("ERR", {"ticker": "ERR"}, MagicMock())
        assert "error" in result
        assert result["failed_stage"] == "moat_analysis"


# ---------------------------------------------------------------------------
# C4: _run_thesis_for_qualifiers
# ---------------------------------------------------------------------------


class TestRunThesisForQualifiers:
    @patch("dashboard.search_runner.thesis_handler")
    def test_thesis_runs_for_each_qualifier(self, mock_thesis):
        mock_thesis.side_effect = lambda e, c: {**e, "thesis_generated": True}
        qualifiers = [
            SearchResult(
                ticker="AAPL", company_name="Apple", moat_score=8,
                management_score=7, margin_of_safety=0.35, intrinsic_value=180.0,
                current_price=120.0, passed_all_gates=True,
                gate_details={"moat": True, "management": True, "mos": True},
                gates_passed_count=3, error=None, raw_result={"ticker": "AAPL"},
            ),
            SearchResult(
                ticker="MSFT", company_name="Microsoft", moat_score=9,
                management_score=8, margin_of_safety=0.40, intrinsic_value=400.0,
                current_price=240.0, passed_all_gates=True,
                gate_details={"moat": True, "management": True, "mos": True},
                gates_passed_count=3, error=None, raw_result={"ticker": "MSFT"},
            ),
        ]
        updated = _run_thesis_for_qualifiers(qualifiers)
        assert mock_thesis.call_count == 2
        assert len(updated) == 2

    @patch("dashboard.search_runner.thesis_handler")
    def test_thesis_failure_does_not_remove_qualifier(self, mock_thesis):
        mock_thesis.side_effect = Exception("Opus budget exhausted")
        qualifiers = [
            SearchResult(
                ticker="AAPL", company_name="Apple", moat_score=8,
                management_score=7, margin_of_safety=0.35, intrinsic_value=180.0,
                current_price=120.0, passed_all_gates=True,
                gate_details={"moat": True, "management": True, "mos": True},
                gates_passed_count=3, error=None, raw_result={"ticker": "AAPL"},
            ),
        ]
        updated = _run_thesis_for_qualifiers(qualifiers)
        assert len(updated) == 1
        assert "thesis" in (updated[0].error or "").lower() or updated[0].error is not None


# ---------------------------------------------------------------------------
# C5: run_search
# ---------------------------------------------------------------------------


def _make_passing_pipeline(*args, **kwargs):
    """Helper that makes pipeline return a passing result."""
    return {
        "ticker": args[0] if args else "TEST",
        "moat_score": 8,
        "management_score": 7,
        "margin_of_safety": 0.35,
        "intrinsic_value_per_share": 180.0,
        "current_price": 120.0,
        "quant_passed": True,
    }


def _make_failing_pipeline(*args, **kwargs):
    """Helper that makes pipeline return a failing result."""
    return {
        "ticker": args[0] if args else "TEST",
        "moat_score": 3,
        "management_score": 2,
        "margin_of_safety": 0.10,
        "quant_passed": True,
    }


class TestRunSearch:
    @patch("dashboard.search_runner.thesis_handler")
    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_stops_at_requested_count(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline, mock_thesis
    ):
        # Set up generator to return tickers
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = [f"T{i}" for i in range(20)]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True
        mock_pipeline.side_effect = lambda t, d, f: _make_passing_pipeline(t)
        mock_thesis.side_effect = lambda e, c: {**e, "thesis_generated": True}

        config = SearchConfig(num_results=2, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        results = run_search(config, progress, cancel)
        matches = [r for r in results if r.passed_all_gates]
        assert len(matches) == 2

    @patch("dashboard.search_runner.thesis_handler")
    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_stops_at_time_limit(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline, mock_thesis
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = [f"T{i}" for i in range(100)]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True
        # Make pipeline slow enough to hit time limit
        mock_pipeline.side_effect = lambda t, d, f: _make_failing_pipeline(t)

        config = SearchConfig(num_results=10, time_limit_minutes=5)
        progress = {}
        cancel = threading.Event()

        # Patch time.monotonic to simulate time passing
        real_monotonic = time.monotonic
        call_count = [0]

        def fake_monotonic():
            call_count[0] += 1
            # After a few calls, return a time past the limit
            if call_count[0] > 6:
                return real_monotonic() + 400
            return real_monotonic()

        with patch("dashboard.search_runner.time.monotonic", side_effect=fake_monotonic):
            results = run_search(config, progress, cancel)

        # Should have stopped due to time limit, not found 10 matches
        matches = [r for r in results if r.passed_all_gates]
        assert len(matches) < 10

    @patch("dashboard.search_runner.thesis_handler")
    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_stops_at_hard_cap(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline, mock_thesis
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = [f"T{i}" for i in range(60)]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True
        mock_pipeline.side_effect = lambda t, d, f: _make_failing_pipeline(t)

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        results = run_search(config, progress, cancel)
        assert len(results) <= 50

    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_stops_on_cancel(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = [f"T{i}" for i in range(20)]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True

        eval_count = [0]

        def counting_pipeline(t, d, f):
            eval_count[0] += 1
            return _make_failing_pipeline(t)

        mock_pipeline.side_effect = counting_pipeline

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        # Set cancel after 2 evals via pipeline side effect
        def cancel_after_2(t, d, f):
            result = counting_pipeline(t, d, f)
            if eval_count[0] >= 2:
                cancel.set()
            return result

        mock_pipeline.side_effect = cancel_after_2

        results = run_search(config, progress, cancel)
        assert len(results) <= 3  # At most 2-3 evals before cancel takes effect

    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_skips_prescreen_failures(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = ["GOOD", "BAD", "GOOD2"]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        def selective_prescreen(ticker):
            if ticker == "BAD":
                return (False, {})
            return (True, {"marketCap": 5e9})

        mock_pre.side_effect = selective_prescreen
        mock_ingest.return_value = True
        mock_pipeline.side_effect = lambda t, d, f: _make_failing_pipeline(t)

        # Return one batch, then empty to stop
        mock_gen.generate_batch.side_effect = [["GOOD", "BAD", "GOOD2"], []]

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        run_search(config, progress, cancel)
        # BAD should not have been sent to pipeline
        pipeline_tickers = [call.args[0] for call in mock_pipeline.call_args_list]
        assert "BAD" not in pipeline_tickers

    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_handles_pipeline_failure(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        mock_gen = MagicMock()
        # Return a batch, then empty to stop
        mock_gen.generate_batch.side_effect = [["ERR", "OK"], []]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True

        def selective_pipeline(t, d, f):
            if t == "ERR":
                raise Exception("Pipeline crash")
            return _make_failing_pipeline(t)

        mock_pipeline.side_effect = selective_pipeline

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        results = run_search(config, progress, cancel)
        # Should have at least the OK result, ERR should have error
        err_results = [r for r in results if r.ticker == "ERR"]
        ok_results = [r for r in results if r.ticker == "OK"]
        assert len(err_results) == 1
        assert err_results[0].error is not None
        assert len(ok_results) == 1

    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_handles_ingestion_failure(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = ["FAIL", "OK"]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})

        def selective_ingest(ticker, cik):
            return ticker != "FAIL"

        mock_ingest.side_effect = selective_ingest
        mock_pipeline.side_effect = lambda t, d, f: _make_failing_pipeline(t)

        # Return one batch, then empty to stop
        mock_gen.generate_batch.side_effect = [["FAIL", "OK"], []]

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        run_search(config, progress, cancel)
        # FAIL should have been skipped — pipeline not called for it
        pipeline_tickers = [call.args[0] for call in mock_pipeline.call_args_list]
        assert "FAIL" not in pipeline_tickers

    @patch("dashboard.search_runner.thesis_handler")
    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_runs_thesis_only_for_qualifiers(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline, mock_thesis
    ):
        mock_gen = MagicMock()
        mock_gen.generate_batch.return_value = ["P1", "P2", "F1", "F2", "F3"]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True

        def selective_pipeline(t, d, f):
            if t in ("P1", "P2"):
                return _make_passing_pipeline(t)
            return _make_failing_pipeline(t)

        mock_pipeline.side_effect = selective_pipeline
        mock_thesis.side_effect = lambda e, c: {**e, "thesis_generated": True}

        config = SearchConfig(num_results=2, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        run_search(config, progress, cancel)
        assert mock_thesis.call_count == 2

    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_search_returns_near_misses(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        mock_gen = MagicMock()
        # Return one batch, then empty to stop
        mock_gen.generate_batch.side_effect = [["NEAR"], []]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True
        # 2 of 3 gates pass (moat and management pass, MoS fails)
        mock_pipeline.return_value = {
            "ticker": "NEAR",
            "moat_score": 8,
            "management_score": 7,
            "margin_of_safety": 0.25,
            "quant_passed": True,
        }

        config = SearchConfig(num_results=10, time_limit_minutes=60)
        progress = {}
        cancel = threading.Event()

        results = run_search(config, progress, cancel)
        near = [r for r in results if r.ticker == "NEAR"]
        assert len(near) == 1
        assert near[0].gates_passed_count == 2
        assert near[0].passed_all_gates is False


# ---------------------------------------------------------------------------
# C6: Progress results memory
# ---------------------------------------------------------------------------


class TestProgressResultsMemory:
    @patch("dashboard.search_runner._run_pipeline_stages")
    @patch("dashboard.search_runner.ingest_ticker_data")
    @patch("dashboard.search_runner.pre_screen_ticker")
    @patch("dashboard.search_runner.CandidateGenerator")
    def test_progress_results_does_not_grow_unbounded(
        self, mock_gen_cls, mock_pre, mock_ingest, mock_pipeline
    ):
        """After evaluating N tickers, progress['results'] must hold at most one
        copy of the list, not N accumulated copies from results[:] slicing."""
        result_list_ids: set[int] = set()

        class TrackingDict(dict):
            """Dict subclass that records object ids of lists stored under 'results'."""

            def update(self, other):
                if "results" in other:
                    result_list_ids.add(id(other["results"]))
                super().update(other)

            def __setitem__(self, key, value):
                if key == "results":
                    result_list_ids.add(id(value))
                super().__setitem__(key, value)

        mock_gen = MagicMock()
        tickers = [f"T{i}" for i in range(10)]
        mock_gen.generate_batch.side_effect = [tickers, []]
        mock_gen.get_cik.return_value = "0000000001"
        mock_gen_cls.return_value = mock_gen

        mock_pre.return_value = (True, {"marketCap": 5e9})
        mock_ingest.return_value = True
        mock_pipeline.side_effect = lambda t, d, f: _make_failing_pipeline(t)

        progress = TrackingDict()
        cancel = threading.Event()
        config = SearchConfig(num_results=10, time_limit_minutes=60)

        run_search(config, progress, cancel)

        # With results[:] creating copies each iteration, we'd get 10+ distinct
        # list ids.  With direct reference assignment, we should get at most 2
        # (the loop reuses the same list object, plus possibly the final update).
        assert len(result_list_ids) <= 2, (
            f"Expected at most 2 distinct result lists, got {len(result_list_ids)}. "
            f"results[:] is creating unnecessary copies each iteration."
        )
