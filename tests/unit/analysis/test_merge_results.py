"""
Unit tests for merge_results handler.
"""

from __future__ import annotations  # noqa: I001

from analysis.merge_results.handler import handler


# Realistic branch outputs matching actual handler key contracts
_MGMT_OUTPUT = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "metrics": {"revenue": 400_000_000_000},
    "moat_score": 8,
    "moat_type": "wide",
    "moat_sources": ["switching costs"],
    "management_score": 7,
    "owner_operator_mindset": 8,
    "capital_allocation_skill": 7,
    "candor_transparency": 6,
    "red_flags": [],
    "green_flags": ["strong buyback program"],
    "reasoning": "Solid management team.",
    "confidence": 0.8,
}

_IV_OUTPUT = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "metrics": {"revenue": 400_000_000_000},
    "moat_score": 8,
    "moat_type": "wide",
    "moat_sources": ["switching costs"],
    "intrinsic_value_per_share": 210.0,
    "margin_of_safety": 0.45,
    "buy_signal": True,
    "scenarios": {"base": {}, "bull": {}, "bear": {}},
    "dcf_per_share": 220.0,
    "epv_per_share": 200.0,
    "floor_per_share": 180.0,
    "mos_threshold": 0.30,
    "current_price": 145.0,
}


class TestMergeResultsHandler:
    """Tests for merge_results handler."""

    def test_merge_two_disjoint_branches(self) -> None:
        """Two-element list with disjoint unique keys merges correctly."""
        result = handler([_MGMT_OUTPUT, _IV_OUTPUT], None)

        # Management-specific keys
        assert result["management_score"] == 7
        assert result["owner_operator_mindset"] == 8
        assert result["capital_allocation_skill"] == 7
        assert result["candor_transparency"] == 6
        assert result["red_flags"] == []
        assert result["green_flags"] == ["strong buyback program"]

        # IV-specific keys
        assert result["intrinsic_value_per_share"] == 210.0
        assert result["margin_of_safety"] == 0.45
        assert result["buy_signal"] is True
        assert result["dcf_per_share"] == 220.0
        assert result["epv_per_share"] == 200.0
        assert result["floor_per_share"] == 180.0
        assert result["mos_threshold"] == 0.30

        # Pass-through keys preserved
        assert result["ticker"] == "AAPL"
        assert result["company_name"] == "Apple Inc."
        assert result["moat_score"] == 8
        assert result["metrics"] == {"revenue": 400_000_000_000}

    def test_overlapping_passthrough_keys_identical_values(self) -> None:
        """Overlapping base keys (ticker, metrics, moat_score) are identical, no data loss."""
        result = handler([_MGMT_OUTPUT, _IV_OUTPUT], None)
        assert result["ticker"] == "AAPL"
        assert result["moat_score"] == 8
        assert result["metrics"] == _MGMT_OUTPUT["metrics"]

    def test_empty_list(self) -> None:
        """Empty list returns empty dict."""
        assert handler([], None) == {}

    def test_single_element_list(self) -> None:
        """Single-element list returns that element unchanged."""
        result = handler([_MGMT_OUTPUT], None)
        assert result == _MGMT_OUTPUT

    def test_non_list_input_returns_as_is(self) -> None:
        """Non-list input logs warning and returns event as-is."""
        event = {"ticker": "AAPL", "unexpected": True}
        result = handler(event, None)
        assert result == event

    def test_merge_preserves_all_keys_from_both_branches(self) -> None:
        """Merged output contains every key from both branch outputs."""
        result = handler([_MGMT_OUTPUT, _IV_OUTPUT], None)
        for key in _MGMT_OUTPUT:
            assert key in result, f"Missing management key: {key}"
        for key in _IV_OUTPUT:
            assert key in result, f"Missing IV key: {key}"

    def test_thesis_required_fields_present(self) -> None:
        """Merged output contains all fields required by thesis generator."""
        result = handler([_MGMT_OUTPUT, _IV_OUTPUT], None)
        thesis_required = [
            "ticker",
            "company_name",
            "metrics",
            "moat_score",
            "management_score",
            "margin_of_safety",
            "intrinsic_value_per_share",
        ]
        for field in thesis_required:
            assert field in result, f"Missing thesis-required field: {field}"
