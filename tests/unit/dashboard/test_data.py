"""Tests for dashboard.data — load_thesis_content and view importability."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestLoadThesisContentImportable:
    """load_thesis_content must be importable from dashboard.data."""

    def test_import_load_thesis_content(self):
        from dashboard.data import load_thesis_content

        assert callable(load_thesis_content)


class TestLoadThesisContentBehavior:
    """load_thesis_content should return thesis markdown or None."""

    @patch("dashboard.data.DynamoClient")
    @patch("dashboard.data.S3Client")
    def test_returns_none_when_no_analysis_items(self, mock_s3_cls, mock_dynamo_cls):
        """When no analysis items exist for a ticker, return None."""
        mock_client = MagicMock()
        mock_client.query.return_value = []
        mock_dynamo_cls.return_value = mock_client

        from dashboard.data import load_thesis_content

        # Clear streamlit cache if present
        if hasattr(load_thesis_content, "clear"):
            load_thesis_content.clear()

        result = load_thesis_content("AAPL")
        assert result is None

    @patch("dashboard.data.DynamoClient")
    @patch("dashboard.data.S3Client")
    def test_returns_none_when_no_thesis_stage(self, mock_s3_cls, mock_dynamo_cls):
        """When analysis items exist but no thesis_generation stage, return None."""
        mock_client = MagicMock()
        mock_client.query.return_value = [
            {
                "ticker": "AAPL",
                "analysis_date": "2026-03-28#moat_analysis",
                "screen_type": "moat_analysis",
                "result": {"moat_score": 8},
                "passed": True,
            }
        ]
        mock_dynamo_cls.return_value = mock_client

        from dashboard.data import load_thesis_content

        if hasattr(load_thesis_content, "clear"):
            load_thesis_content.clear()

        result = load_thesis_content("AAPL")
        assert result is None

    @patch("dashboard.data.S3Client")
    @patch("dashboard.data.DynamoClient")
    def test_returns_thesis_markdown_from_s3(self, mock_dynamo_cls, mock_s3_cls):
        """When thesis_generation stage exists with s3 key, read and return markdown."""
        mock_client = MagicMock()
        mock_client.query.return_value = [
            {
                "ticker": "AAPL",
                "analysis_date": "2026-03-28#thesis_generation",
                "screen_type": "thesis_generation",
                "result": {
                    "thesis_s3_key": "theses/AAPL/2026-03-28.md",
                    "thesis_generated": True,
                },
                "passed": True,
            }
        ]
        mock_dynamo_cls.return_value = mock_client

        mock_s3 = MagicMock()
        mock_s3.read_markdown.return_value = "# AAPL Investment Thesis\n\nGreat company."
        mock_s3_cls.return_value = mock_s3

        from dashboard.data import load_thesis_content

        if hasattr(load_thesis_content, "clear"):
            load_thesis_content.clear()

        result = load_thesis_content("AAPL")
        assert result == "# AAPL Investment Thesis\n\nGreat company."
        mock_s3.read_markdown.assert_called_once_with("theses/AAPL/2026-03-28.md")

    @patch("dashboard.data.DynamoClient")
    @patch("dashboard.data.S3Client")
    def test_returns_none_on_s3_error(self, mock_s3_cls, mock_dynamo_cls):
        """When S3 read fails, return None instead of crashing."""
        mock_client = MagicMock()
        mock_client.query.return_value = [
            {
                "ticker": "AAPL",
                "analysis_date": "2026-03-28#thesis_generation",
                "screen_type": "thesis_generation",
                "result": {
                    "thesis_s3_key": "theses/AAPL/2026-03-28.md",
                    "thesis_generated": True,
                },
                "passed": True,
            }
        ]
        mock_dynamo_cls.return_value = mock_client

        mock_s3 = MagicMock()
        mock_s3.read_markdown.side_effect = Exception("S3 error")
        mock_s3_cls.return_value = mock_s3

        from dashboard.data import load_thesis_content

        if hasattr(load_thesis_content, "clear"):
            load_thesis_content.clear()

        result = load_thesis_content("AAPL")
        assert result is None


class TestDashboardViewsImportable:
    """All dashboard views should be importable without crashing."""

    def test_signals_view_importable(self):
        from dashboard.views import signals

        assert hasattr(signals, "render")

    def test_position_detail_view_importable(self):
        from dashboard.views import position_detail

        assert hasattr(position_detail, "render_position_detail")

    def test_backtest_view_importable(self):
        from dashboard.views import backtest

        assert hasattr(backtest, "render")


class TestPositionDetailUsesThesisLoader:
    """position_detail.py should call load_thesis_content, not hardcode 'not found'."""

    def test_position_detail_references_load_thesis_content(self):
        """The source code should import and call load_thesis_content."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "dashboard"
            / "views"
            / "position_detail.py"
        ).read_text()
        assert "load_thesis_content" in source, (
            "position_detail.py does not reference load_thesis_content — "
            "the thesis tab hardcodes 'No investment thesis found' instead "
            "of attempting to load the thesis from the analysis pipeline."
        )
