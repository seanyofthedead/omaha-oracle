"""Tests for search configuration and quality gate logic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dashboard.search_config import (
    SearchConfig,
    check_quality_gates,
    count_gates_passed,
)

# ---------------------------------------------------------------------------
# A1: SearchConfig Pydantic model
# ---------------------------------------------------------------------------


class TestSearchConfig:
    def test_search_config_defaults(self):
        cfg = SearchConfig()
        assert cfg.num_results == 3
        assert cfg.time_limit_minutes == 15

    def test_search_config_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            SearchConfig(num_results=0)
        with pytest.raises(ValidationError):
            SearchConfig(num_results=11)
        with pytest.raises(ValidationError):
            SearchConfig(time_limit_minutes=61)

    def test_search_config_accepts_valid_range(self):
        cfg = SearchConfig(num_results=5, time_limit_minutes=30)
        assert cfg.num_results == 5
        assert cfg.time_limit_minutes == 30


# ---------------------------------------------------------------------------
# A2: check_quality_gates
# ---------------------------------------------------------------------------


class TestCheckQualityGates:
    def test_check_gates_all_pass(self):
        result = {"moat_score": 8, "management_score": 7, "margin_of_safety": 0.35}
        passed, details = check_quality_gates(result)
        assert passed is True
        assert details == {"moat": True, "management": True, "mos": True}

    def test_check_gates_moat_fails(self):
        result = {"moat_score": 5, "management_score": 7, "margin_of_safety": 0.35}
        passed, details = check_quality_gates(result)
        assert passed is False
        assert details["moat"] is False
        assert details["management"] is True
        assert details["mos"] is True

    def test_check_gates_mgmt_fails(self):
        result = {"moat_score": 8, "management_score": 4, "margin_of_safety": 0.35}
        passed, details = check_quality_gates(result)
        assert passed is False
        assert details["management"] is False

    def test_check_gates_mos_fails(self):
        result = {"moat_score": 8, "management_score": 7, "margin_of_safety": 0.25}
        passed, details = check_quality_gates(result)
        assert passed is False
        assert details["mos"] is False

    def test_check_gates_mos_boundary(self):
        """MoS of exactly 0.30 must fail (strictly > 0.30 required)."""
        result = {"moat_score": 8, "management_score": 7, "margin_of_safety": 0.30}
        passed, details = check_quality_gates(result)
        assert passed is False
        assert details["mos"] is False

    def test_check_gates_near_miss(self):
        result = {"moat_score": 8, "management_score": 7, "margin_of_safety": 0.25}
        passed, details = check_quality_gates(result)
        assert passed is False
        assert details["moat"] is True
        assert details["management"] is True
        assert details["mos"] is False


# ---------------------------------------------------------------------------
# A3: count_gates_passed
# ---------------------------------------------------------------------------


class TestCountGatesPassed:
    def test_count_gates_passed(self):
        assert count_gates_passed({"moat": True, "management": True, "mos": False}) == 2

    def test_count_gates_all_pass(self):
        assert count_gates_passed({"moat": True, "management": True, "mos": True}) == 3

    def test_count_gates_none_pass(self):
        assert count_gates_passed({"moat": False, "management": False, "mos": False}) == 0
