"""Test that build_analysis_event sets quant_passed=False for manual uploads."""

from __future__ import annotations

from dashboard.analysis_runner import build_analysis_event
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


class TestQuantPassedFalse:
    """Manual uploads must NOT bypass the quant screen circuit breaker."""

    def test_build_event_sets_quant_passed_false(self):
        """quant_passed must be False so portfolio risk guardrails are not bypassed."""
        event = build_analysis_event(_meta(), "uploads/AAPL/2025/10-K/file.pdf")
        assert event["quant_passed"] is False, (
            "Manual uploads must set quant_passed=False to avoid bypassing "
            "the portfolio circuit breaker that blocks BUYs without a quant screen."
        )
