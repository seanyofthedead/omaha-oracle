"""
Unit tests for shared.converters — pure stdlib, no AWS, no moto required.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from shared.converters import (
    check_failure_threshold,
    format_metrics,
    normalize_ticker,
    safe_float,
    safe_int,
    today_str,
)


class TestSafeFloat:
    def test_none_returns_default(self) -> None:
        assert safe_float(None) == 0.0

    def test_none_with_custom_default(self) -> None:
        assert safe_float(None, default=-1.0) == -1.0

    def test_decimal(self) -> None:
        assert safe_float(Decimal("3.14")) == pytest.approx(3.14)

    def test_float_passthrough(self) -> None:
        assert safe_float(1.5) == 1.5

    def test_int(self) -> None:
        assert safe_float(42) == 42.0

    def test_numeric_string(self) -> None:
        assert safe_float("2.5") == 2.5

    def test_invalid_string_returns_default(self) -> None:
        assert safe_float("not-a-number") == 0.0

    def test_invalid_string_custom_default(self) -> None:
        assert safe_float("bad", default=-99.0) == -99.0

    def test_empty_string_returns_default(self) -> None:
        assert safe_float("") == 0.0

    def test_zero_decimal(self) -> None:
        assert safe_float(Decimal("0")) == 0.0

    def test_negative(self) -> None:
        assert safe_float(-5.5) == -5.5


class TestSafeInt:
    def test_none_returns_default(self) -> None:
        assert safe_int(None) == 0

    def test_none_with_custom_default(self) -> None:
        assert safe_int(None, default=-1) == -1

    def test_decimal(self) -> None:
        assert safe_int(Decimal("7")) == 7

    def test_float(self) -> None:
        assert safe_int(3.9) == 3

    def test_float_string(self) -> None:
        assert safe_int("4.7") == 4

    def test_int_string(self) -> None:
        assert safe_int("10") == 10

    def test_invalid_string_returns_default(self) -> None:
        assert safe_int("abc") == 0

    def test_negative(self) -> None:
        assert safe_int(-3.8) == -3


class TestTodayStr:
    def test_returns_yyyy_mm_dd(self) -> None:
        result = today_str()
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result), f"Bad format: {result}"

    def test_is_string(self) -> None:
        assert isinstance(today_str(), str)


class TestNormalizeTicker:
    def test_uppercase(self) -> None:
        assert normalize_ticker({"ticker": "aapl"}) == "AAPL"

    def test_strips_whitespace(self) -> None:
        assert normalize_ticker({"ticker": "  MSFT  "}) == "MSFT"

    def test_none_value(self) -> None:
        assert normalize_ticker({"ticker": None}) == ""

    def test_missing_key(self) -> None:
        assert normalize_ticker({}) == ""

    def test_empty_string(self) -> None:
        assert normalize_ticker({"ticker": ""}) == ""

    def test_mixed_case_with_spaces(self) -> None:
        assert normalize_ticker({"ticker": " gOoG "}) == "GOOG"


class TestCheckFailureThreshold:
    def test_below_threshold_does_not_raise(self) -> None:
        # 2 errors out of 10 = 20%, below 50% threshold
        check_failure_threshold(["e1", "e2"], 10, "module")

    def test_at_threshold_does_not_raise(self) -> None:
        # exactly 50%: len(errors)=5, total=10, 5 > 5 is False
        check_failure_threshold(["e"] * 5, 10, "module")

    def test_above_threshold_raises(self) -> None:
        # 6 errors out of 10 = 60%, above 50% threshold
        with pytest.raises(RuntimeError, match="module"):
            check_failure_threshold(["e"] * 6, 10, "module")

    def test_zero_total_does_not_raise(self) -> None:
        check_failure_threshold(["e1"], 0, "module")

    def test_custom_threshold(self) -> None:
        # With 25% threshold, 3/10 = 30% should raise
        with pytest.raises(RuntimeError):
            check_failure_threshold(["e"] * 3, 10, "module", threshold=0.25)

    def test_error_message_includes_counts(self) -> None:
        with pytest.raises(RuntimeError, match=r"6/10"):
            check_failure_threshold(["e"] * 6, 10, "mymod")

    def test_error_message_includes_first_errors(self) -> None:
        errors = ["err_a", "err_b", "err_c", "err_d", "err_e", "err_f"]
        with pytest.raises(RuntimeError, match="err_a"):
            check_failure_threshold(errors, 10, "mod")


class TestFormatMetrics:
    def test_empty_dict_returns_placeholder(self) -> None:
        assert format_metrics({}) == "No metrics provided."

    def test_none_returns_placeholder(self) -> None:
        assert format_metrics(None) == "No metrics provided."  # type: ignore[arg-type]

    def test_valid_dict_returns_json(self) -> None:
        result = format_metrics({"pe": 12.5, "pb": 1.2})
        assert '"pe"' in result
        assert "12.5" in result

    def test_indented_output(self) -> None:
        result = format_metrics({"a": 1})
        assert "\n" in result  # indent=2 produces newlines

    def test_handles_non_serialisable_with_default_str(self) -> None:
        from decimal import Decimal

        result = format_metrics({"val": Decimal("3.14")})
        assert "3.14" in result
