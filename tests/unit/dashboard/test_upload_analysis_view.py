"""Tests for the Upload Analysis view module."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# View skeleton
# ---------------------------------------------------------------------------


class TestUploadAnalysisView:
    def test_module_importable(self):
        mod = importlib.import_module("dashboard.views.upload_analysis")
        assert mod is not None

    def test_module_has_render_callable(self):
        mod = importlib.import_module("dashboard.views.upload_analysis")
        assert callable(getattr(mod, "render", None)), (
            "upload_analysis module must expose a callable render() — "
            "app.py calls page.render() for every page."
        )

    def test_module_has_display_results_callable(self):
        mod = importlib.import_module("dashboard.views.upload_analysis")
        assert callable(getattr(mod, "_display_results", None))


# ---------------------------------------------------------------------------
# Results display
# ---------------------------------------------------------------------------


class TestResultsDisplay:
    def _get_display_fn(self):
        mod = importlib.import_module("dashboard.views.upload_analysis")
        return mod._display_results

    @patch("dashboard.views.upload_analysis.st")
    def test_display_handles_complete_result(self, mock_st):
        """A fully populated result dict should not raise."""
        # columns() is called with 5 (hero) and 3 (sub-tabs) — return enough
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]
        result = {
            "ticker": "AAPL",
            "moat_score": 8,
            "moat_type": "wide",
            "moat_sources": ["switching costs"],
            "moat_trend": "stable",
            "pricing_power": 7,
            "customer_captivity": 8,
            "reasoning": "Strong ecosystem.",
            "risks_to_moat": ["regulation"],
            "management_score": 7,
            "owner_operator_mindset": 8,
            "capital_allocation_skill": 7,
            "candor_transparency": 6,
            "green_flags": ["buybacks"],
            "red_flags": [],
            "intrinsic_value_per_share": 142.50,
            "margin_of_safety": 0.325,
            "dcf_per_share": 150.0,
            "epv_per_share": 130.0,
            "floor_per_share": 80.0,
            "current_price": 100.0,
            "thesis_generated": True,
            "thesis_s3_key": "theses/AAPL/2026-03-21.md",
        }
        fn = self._get_display_fn()
        fn(result)  # Should not raise

    @patch("dashboard.views.upload_analysis.st")
    def test_display_handles_empty_result(self, mock_st):
        """An empty result dict should not raise."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]
        fn = self._get_display_fn()
        fn({})  # Should not raise


# ---------------------------------------------------------------------------
# Error display
# ---------------------------------------------------------------------------


class TestErrorDisplay:
    def _get_handle_fn(self):
        mod = importlib.import_module("dashboard.views.upload_analysis")
        return mod._handle_submission

    @patch("dashboard.views.upload_analysis.st")
    def test_no_file_shows_error(self, mock_st):
        """Submitting without a file should call st.error."""
        fn = self._get_handle_fn()
        fn(None, "AAPL", "Apple Inc.", "10-K", 2025, "", "")
        mock_st.error.assert_called_once()
        assert "upload" in mock_st.error.call_args[0][0].lower()

    @patch("dashboard.views.upload_analysis.st")
    def test_validation_errors_shown(self, mock_st):
        """An invalid file should call st.error with validation message."""
        fake_file = MagicMock()
        fake_file.name = "report.docx"
        fake_file.size = 1024
        fn = self._get_handle_fn()
        fn(fake_file, "AAPL", "Apple Inc.", "10-K", 2025, "", "")
        mock_st.error.assert_called()
        # Should mention unsupported file type
        args = [c[0][0] for c in mock_st.error.call_args_list]
        assert any("Unsupported" in a for a in args)

    @patch("dashboard.views.upload_analysis.st")
    def test_empty_ticker_shows_error(self, mock_st):
        """Empty ticker should trigger metadata validation error."""
        fake_file = MagicMock()
        fake_file.name = "report.pdf"
        fake_file.size = 1024
        fn = self._get_handle_fn()
        fn(fake_file, "", "Apple Inc.", "10-K", 2025, "", "")
        mock_st.error.assert_called()

    @patch("dashboard.views.upload_analysis.st")
    def test_partial_results_display(self, mock_st):
        """A result with failed_stage should still display without crashing."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]
        mod = importlib.import_module("dashboard.views.upload_analysis")
        result = {
            "ticker": "AAPL",
            "moat_score": 8,
            "moat_type": "wide",
            "failed_stage": "management_quality",
            "error": "timeout",
        }
        mod._display_results(result)  # Should not raise
