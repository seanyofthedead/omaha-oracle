"""Tests for the analysis runner (event builder + orchestrator)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from dashboard.analysis_runner import (
    build_analysis_event,
    format_analysis_summary,
    run_upload_analysis,
)
from dashboard.upload_validator import UploadMetadata


def _meta(**overrides) -> UploadMetadata:
    defaults = dict(
        ticker="AAPL",
        company_name="Apple Inc.",
        filing_type="10-K",
        fiscal_year=2025,
    )
    defaults.update(overrides)
    return UploadMetadata(**defaults)


# ---------------------------------------------------------------------------
# Event builder
# ---------------------------------------------------------------------------


class TestEventBuilder:
    def test_build_event_contains_ticker(self):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["ticker"] == "AAPL"

    def test_build_event_contains_company_name(self):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["company_name"] == "Apple Inc."

    def test_build_event_contains_metrics_stub(self):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert isinstance(event["metrics"], dict)
        assert "sector" in event["metrics"]
        assert "industry" in event["metrics"]

    def test_build_event_marks_manual_upload(self):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["source"] == "manual_upload"
        assert event["upload_s3_key"] == "uploads/AAPL/2025/10-K/file.pdf"

    def test_build_event_sets_quant_passed_true(self):
        """Manual uploads skip quant screen — quant_passed must be True."""
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["quant_passed"] is True

    def test_build_event_with_optional_metrics(self):
        extra = {"sector": "Technology", "industry": "Consumer Electronics", "currentPrice": 180.0}
        event = build_analysis_event(
            _meta(), "uploads/AAPL/2025/10-K/file.pdf", extra_metrics=extra
        )
        assert event["metrics"]["sector"] == "Technology"
        assert event["metrics"]["industry"] == "Consumer Electronics"
        assert event["metrics"]["currentPrice"] == 180.0

    def test_build_event_default_metrics_are_unknown(self):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["metrics"]["sector"] == "Unknown"
        assert event["metrics"]["industry"] == "Unknown"


# ---------------------------------------------------------------------------
# Analysis runner orchestrator
# ---------------------------------------------------------------------------

# Module paths for the four handler functions called by the runner
_MOAT = "dashboard.analysis_runner.moat_handler"
_MGMT = "dashboard.analysis_runner.mgmt_handler"
_IV = "dashboard.analysis_runner.iv_handler"
_THESIS = "dashboard.analysis_runner.thesis_handler"


def _make_handler_mock(extra_fields: dict):
    """Return a mock handler that merges extra_fields into its input event."""

    def _handler(event, context):
        result = dict(event)
        result.update(extra_fields)
        return result

    return MagicMock(side_effect=_handler)


class TestAnalysisRunner:
    def _patch_all(self):
        """Return a dict of patches for all four handlers."""
        return {
            "moat": _make_handler_mock({"moat_score": 8, "moat_type": "wide"}),
            "mgmt": _make_handler_mock({"management_score": 7}),
            "iv": _make_handler_mock(
                {"intrinsic_value_per_share": 150.0, "margin_of_safety": 0.35}
            ),
            "thesis": _make_handler_mock(
                {"thesis_s3_key": "theses/AAPL/2026-03-21.md", "thesis_generated": True}
            ),
        }

    def _run_with_patches(self, mocks, event=None, filing_context="Test context", progress_cb=None):
        event = event or build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        with (
            patch(_MOAT, mocks["moat"]),
            patch(_MGMT, mocks["mgmt"]),
            patch(_IV, mocks["iv"]),
            patch(_THESIS, mocks["thesis"]),
        ):
            return run_upload_analysis(event, filing_context, progress_callback=progress_cb)

    def test_calls_moat_first(self):
        mocks = self._patch_all()
        self._run_with_patches(mocks)
        mocks["moat"].assert_called_once()
        args = mocks["moat"].call_args[0]
        assert args[0]["ticker"] == "AAPL"

    def test_moat_output_flows_to_management(self):
        mocks = self._patch_all()
        self._run_with_patches(mocks)
        mgmt_input = mocks["mgmt"].call_args[0][0]
        assert mgmt_input["moat_score"] == 8

    def test_management_flows_to_iv(self):
        mocks = self._patch_all()
        self._run_with_patches(mocks)
        iv_input = mocks["iv"].call_args[0][0]
        assert iv_input["management_score"] == 7

    def test_iv_flows_to_thesis(self):
        mocks = self._patch_all()
        self._run_with_patches(mocks)
        thesis_input = mocks["thesis"].call_args[0][0]
        assert thesis_input["margin_of_safety"] == 0.35

    def test_returns_final_result(self):
        mocks = self._patch_all()
        result = self._run_with_patches(mocks)
        assert result["thesis_s3_key"] == "theses/AAPL/2026-03-21.md"
        assert result["thesis_generated"] is True

    def test_progress_callback_called_per_stage(self):
        mocks = self._patch_all()
        cb = MagicMock()
        self._run_with_patches(mocks, progress_cb=cb)
        assert cb.call_count == 4
        cb.assert_any_call("moat_analysis", 1)
        cb.assert_any_call("management_quality", 2)
        cb.assert_any_call("intrinsic_value", 3)
        cb.assert_any_call("thesis_generator", 4)


# ---------------------------------------------------------------------------
# Results formatter
# ---------------------------------------------------------------------------


class TestResultsFormatter:
    def test_format_scores_for_display(self):
        result = {
            "moat_score": 8,
            "moat_type": "wide",
            "management_score": 7,
            "intrinsic_value_per_share": 142.50,
            "margin_of_safety": 0.325,
            "thesis_generated": True,
        }
        summary = format_analysis_summary(result)
        assert "8" in summary["moat_display"]
        assert "wide" in summary["moat_display"].lower()
        assert "7" in summary["management_display"]
        assert "$142" in summary["iv_display"]
        assert "32" in summary["mos_display"]  # 32.5%

    def test_format_handles_missing_scores(self):
        result = {"moat_score": 0}
        summary = format_analysis_summary(result)
        assert summary["moat_display"] == "\u2014"  # em-dash

    def test_format_handles_skipped_stages(self):
        result = {"moat_score": 0, "skipped": True}
        summary = format_analysis_summary(result)
        assert "skipped" in summary["moat_display"].lower()

    def test_format_handles_failed_stages(self):
        result = {"failed_stage": "moat_analysis", "error": "API error"}
        summary = format_analysis_summary(result)
        assert "error" in summary["moat_display"].lower() or "\u2014" in summary["moat_display"]


# ---------------------------------------------------------------------------
# Stage failure handling
# ---------------------------------------------------------------------------


class TestStageFailures:
    def _patch_all(self):
        return {
            "moat": _make_handler_mock({"moat_score": 8, "moat_type": "wide"}),
            "mgmt": _make_handler_mock({"management_score": 7}),
            "iv": _make_handler_mock(
                {"intrinsic_value_per_share": 150.0, "margin_of_safety": 0.35}
            ),
            "thesis": _make_handler_mock(
                {"thesis_s3_key": "theses/AAPL/2026-03-21.md", "thesis_generated": True}
            ),
        }

    def _run(self, mocks):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        with (
            patch(_MOAT, mocks["moat"]),
            patch(_MGMT, mocks["mgmt"]),
            patch(_IV, mocks["iv"]),
            patch(_THESIS, mocks["thesis"]),
        ):
            return run_upload_analysis(event, "Test context")

    def test_moat_failure_returns_partial_with_error(self):
        mocks = self._patch_all()
        mocks["moat"].side_effect = Exception("API error")
        result = self._run(mocks)
        assert result["failed_stage"] == "moat_analysis"
        assert "API error" in result["error"]

    def test_management_failure_preserves_moat_result(self):
        mocks = self._patch_all()
        mocks["mgmt"].side_effect = Exception("timeout")
        result = self._run(mocks)
        assert result["moat_score"] == 8
        assert result["failed_stage"] == "management_quality"

    def test_iv_failure_preserves_prior_stages(self):
        mocks = self._patch_all()
        mocks["iv"].side_effect = Exception("math error")
        result = self._run(mocks)
        assert result["moat_score"] == 8
        assert result["management_score"] == 7
        assert result["failed_stage"] == "intrinsic_value"

    def test_thesis_failure_preserves_all_scores(self):
        mocks = self._patch_all()
        mocks["thesis"].side_effect = Exception("LLM error")
        result = self._run(mocks)
        assert result["moat_score"] == 8
        assert result["management_score"] == 7
        assert result["margin_of_safety"] == 0.35
        assert result["failed_stage"] == "thesis_generator"
        assert result.get("thesis_generated") is not True


# ---------------------------------------------------------------------------
# Progress tracking (thorough contract tests)
# ---------------------------------------------------------------------------


class TestProgressTracking:
    def _patch_all(self):
        return {
            "moat": _make_handler_mock({"moat_score": 8}),
            "mgmt": _make_handler_mock({"management_score": 7}),
            "iv": _make_handler_mock({"intrinsic_value_per_share": 150.0}),
            "thesis": _make_handler_mock({"thesis_generated": True}),
        }

    def _run(self, mocks, cb=None):
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        with (
            patch(_MOAT, mocks["moat"]),
            patch(_MGMT, mocks["mgmt"]),
            patch(_IV, mocks["iv"]),
            patch(_THESIS, mocks["thesis"]),
        ):
            return run_upload_analysis(event, "Test context", progress_callback=cb)

    def test_callback_receives_stage_name_and_index(self):
        mocks = self._patch_all()
        cb = MagicMock()
        self._run(mocks, cb=cb)
        expected = [
            call("moat_analysis", 1),
            call("management_quality", 2),
            call("intrinsic_value", 3),
            call("thesis_generator", 4),
        ]
        assert cb.call_args_list == expected

    def test_no_callback_does_not_error(self):
        mocks = self._patch_all()
        result = self._run(mocks, cb=None)
        assert result["thesis_generated"] is True

    def test_callback_stops_at_failed_stage(self):
        """If stage 2 fails, callback should only be called for stages 1-2."""
        mocks = self._patch_all()
        mocks["mgmt"].side_effect = Exception("fail")
        cb = MagicMock()
        self._run(mocks, cb=cb)
        assert cb.call_count == 2
        cb.assert_any_call("moat_analysis", 1)
        cb.assert_any_call("management_quality", 2)
