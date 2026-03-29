"""Test that LLM-sourced strings are HTML-escaped before rendering as Markdown."""

from __future__ import annotations

import html
import importlib
from unittest.mock import MagicMock, patch


def _make_mock_st():
    """Return a mock st module that records markdown calls."""
    mock_st = MagicMock()
    mock_st.columns.side_effect = lambda *a, **kw: [
        MagicMock() for _ in range(a[0] if a else 5)
    ]
    mock_st.tabs.return_value = [MagicMock() for _ in range(4)]
    return mock_st


class TestXssSanitization:
    """LLM-generated strings must be HTML-escaped before st.markdown()."""

    @patch("dashboard.views.upload_analysis.st")
    def test_moat_reasoning_is_escaped(self, mock_st):
        """Malicious reasoning string must be escaped in moat tab."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]

        xss_payload = '<script>alert("xss")</script>'
        result = {
            "moat_score": 8,
            "moat_type": "wide",
            "pricing_power": 7,
            "customer_captivity": 8,
            "reasoning": xss_payload,
        }

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._render_moat_tab(result)

        # Collect all st.markdown calls
        md_calls = [c[0][0] for c in mock_st.markdown.call_args_list]
        for call_text in md_calls:
            if "Reasoning" in call_text:
                assert "<script>" not in call_text, (
                    f"Raw <script> tag found in markdown output: {call_text}"
                )
                assert html.escape(xss_payload) in call_text or "&lt;script&gt;" in call_text

    @patch("dashboard.views.upload_analysis.st")
    def test_management_reasoning_is_escaped(self, mock_st):
        """Malicious reasoning in management tab must be escaped."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]

        xss_payload = '<img src=x onerror=alert(1)>'
        result = {
            "management_score": 7,
            "owner_operator_mindset": 8,
            "capital_allocation_skill": 7,
            "candor_transparency": 6,
            "reasoning": xss_payload,
        }

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._render_management_tab(result)

        md_calls = [c[0][0] for c in mock_st.markdown.call_args_list]
        for call_text in md_calls:
            if "Reasoning" in call_text:
                assert "<img " not in call_text, (
                    f"Raw <img> tag found in markdown output: {call_text}"
                )

    @patch("dashboard.views.upload_analysis.st")
    def test_moat_sources_are_escaped(self, mock_st):
        """Moat sources list items must be escaped."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]

        xss_source = '<script>steal()</script>'
        result = {
            "moat_score": 8,
            "moat_type": "wide",
            "pricing_power": 7,
            "customer_captivity": 8,
            "reasoning": "Fine",
            "moat_sources": [xss_source],
        }

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._render_moat_tab(result)

        md_calls = [c[0][0] for c in mock_st.markdown.call_args_list]
        for call_text in md_calls:
            if "Sources" in call_text:
                assert "<script>" not in call_text

    @patch("dashboard.views.upload_analysis.st")
    def test_risks_are_escaped(self, mock_st):
        """Risk items must be escaped."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]

        xss_risk = '<iframe src="evil.com"></iframe>'
        result = {
            "moat_score": 8,
            "moat_type": "wide",
            "pricing_power": 7,
            "customer_captivity": 8,
            "reasoning": "Fine",
            "risks_to_moat": [xss_risk],
        }

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._render_moat_tab(result)

        md_calls = [c[0][0] for c in mock_st.markdown.call_args_list]
        for call_text in md_calls:
            if "Risks" in call_text:
                assert "<iframe" not in call_text

    @patch("dashboard.views.upload_analysis.st")
    def test_green_red_flags_are_escaped(self, mock_st):
        """Green/red flag items must be escaped."""
        mock_st.columns.side_effect = lambda *a, **kw: [
            MagicMock() for _ in range(a[0] if a else 5)
        ]
        mock_st.tabs.return_value = [MagicMock() for _ in range(4)]

        xss_flag = '<script>document.cookie</script>'
        result = {
            "management_score": 7,
            "owner_operator_mindset": 8,
            "capital_allocation_skill": 7,
            "candor_transparency": 6,
            "reasoning": "Fine",
            "green_flags": [xss_flag],
            "red_flags": [xss_flag],
        }

        mod = importlib.import_module("dashboard.views.upload_analysis")
        mod._render_management_tab(result)

        md_calls = [c[0][0] for c in mock_st.markdown.call_args_list]
        for call_text in md_calls:
            assert "<script>" not in call_text
