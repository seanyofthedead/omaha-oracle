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


class TestPdfWarning:
    """After uploading a PDF, st.warning must be called with extraction-limitation notice."""

    @patch("dashboard.views.upload_analysis.store_uploaded_file", return_value="uploads/AAPL/2025/10-K/ts.pdf")
    @patch("dashboard.views.upload_analysis.run_upload_analysis", return_value={"ticker": "AAPL"})
    @patch("dashboard.views.upload_analysis.build_analysis_event", return_value={"ticker": "AAPL"})
    @patch("dashboard.views.upload_analysis.extract_filing_text")
    @patch("dashboard.views.upload_analysis.validate_upload", return_value=[])
    @patch("dashboard.views.upload_analysis.st")
    def test_upload_page_shows_pdf_warning(
        self, mock_st, mock_validate, mock_extract, mock_build, mock_run, mock_store
    ):
        """After uploading a PDF, st.warning must be called with extraction-limitation notice."""
        mock_extract.return_value = (
            "Uploaded filing: 10-K for Apple Inc. (AAPL), FY2025\n\n"
            "PDF filing uploaded. Full text extraction is not yet supported."
        )
        mock_st.status.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.status.return_value.__exit__ = MagicMock(return_value=False)

        fake_file = MagicMock()
        fake_file.name = "report.pdf"
        fake_file.size = 1024
        fake_file.getvalue.return_value = b"%PDF-1.4 binary"

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._handle_submission(fake_file, "AAPL", "Apple Inc.", "10-K", 2025, "", "")

        # st.warning must have been called with extraction-limitation notice
        warning_calls = [c[0][0] for c in mock_st.warning.call_args_list]
        assert any("not yet supported" in w.lower() for w in warning_calls), (
            f"Expected st.warning about PDF extraction limitation, got: {warning_calls}"
        )


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

    @patch("dashboard.views.upload_analysis.extract_filing_text", return_value="context")
    @patch("dashboard.views.upload_analysis.validate_upload", return_value=[])
    @patch("dashboard.views.upload_analysis.st")
    def test_pipeline_error_hides_internal_details(self, mock_st, mock_validate, mock_extract):
        """A sanitized error message must not contain internal paths, env vars, or API key refs."""
        mock_st.status.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.status.return_value.__exit__ = MagicMock(return_value=False)

        # Make store_uploaded_file raise with internal details
        internal_msg = (
            "ConnectionError: Failed connecting to https://s3.amazonaws.com "
            "at /home/deploy/.venv/lib/python3.11/site-packages/botocore/endpoint.py "
            "ANTHROPIC_API_KEY=sk-ant-12345"
        )
        with patch(
            "dashboard.views.upload_analysis.store_uploaded_file",
            side_effect=Exception(internal_msg),
        ):
            fake_file = MagicMock()
            fake_file.name = "report.html"
            fake_file.size = 1024
            fake_file.getvalue.return_value = b"<html>data</html>"

            fn = self._get_handle_fn()
            fn(fake_file, "AAPL", "Apple Inc.", "10-K", 2025, "", "")

        # st.error must have been called, but NOT with the internal details
        error_calls = [c[0][0] for c in mock_st.error.call_args_list]
        for msg in error_calls:
            assert "/home/deploy" not in msg, f"Internal path leaked: {msg}"
            assert "ANTHROPIC_API_KEY" not in msg, f"API key ref leaked: {msg}"
            assert "sk-ant-" not in msg, f"API key value leaked: {msg}"
            assert "endpoint.py" not in msg, f"Internal file path leaked: {msg}"

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
