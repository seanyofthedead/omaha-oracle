"""
TDD tests for analysis consistency bug fixes.

Bug 1: owner_earnings formula duplicated — should use shared compute_owner_earnings.
Bug 2: Quant screen handler duplicates threshold logic — screen_company should return failed_criteria.
Bug 3: Thesis generator hardcodes MOS_MIN=0.30 instead of reading from event.
Bug 4: Moat handler stores zero-score result before re-raising on error.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ------------------------------------------------------------------ #
# Bug 1: Shared compute_owner_earnings                                #
# ------------------------------------------------------------------ #


class TestComputeOwnerEarnings:
    """compute_owner_earnings should live in shared.converters and be used by both handlers."""

    def test_function_exists_in_converters(self):
        from shared.converters import compute_owner_earnings

        assert callable(compute_owner_earnings)

    def test_constant_exists_in_converters(self):
        from shared.converters import MAINTENANCE_CAPEX_FACTOR

        assert MAINTENANCE_CAPEX_FACTOR == 0.7

    def test_basic_calculation(self):
        from shared.converters import compute_owner_earnings

        # ni + dep - 0.7 * capex = 50_000 + 10_000 - 0.7 * 20_000 = 46_000
        assert compute_owner_earnings(50_000, 10_000, 20_000) == pytest.approx(46_000.0)

    def test_zero_capex(self):
        from shared.converters import compute_owner_earnings

        assert compute_owner_earnings(100, 50, 0) == pytest.approx(150.0)

    def test_negative_net_income(self):
        from shared.converters import compute_owner_earnings

        assert compute_owner_earnings(-10_000, 5_000, 10_000) == pytest.approx(-12_000.0)

    def test_screener_uses_shared_function(self):
        """screener.py should import compute_owner_earnings from shared.converters."""
        import inspect

        from analysis.quant_screen import screener

        source = inspect.getsource(screener)
        assert "compute_owner_earnings" in source
        # Should NOT have the raw 0.7 * capex magic constant anymore
        assert "0.7 * capex" not in source

    def test_iv_handler_uses_shared_function(self):
        """intrinsic_value handler should import compute_owner_earnings from shared.converters."""
        import inspect

        from analysis.intrinsic_value import handler

        source = inspect.getsource(handler)
        assert "compute_owner_earnings" in source
        # Should NOT have the raw 0.7 * capex magic constant anymore
        assert "0.7 * capex" not in source


# ------------------------------------------------------------------ #
# Bug 2: screen_company returns failed_criteria list                   #
# ------------------------------------------------------------------ #


class TestScreenCompanyFailedCriteria:
    """screen_company should return failed_criteria in its result dict."""

    def test_result_contains_failed_criteria_key(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")

        from unittest.mock import MagicMock, patch

        from analysis.quant_screen.screener import screen_company

        fin_client = MagicMock()
        # Return empty from query — we'll patch _aggregate_financials_by_year instead
        fin_client.query.return_value = []

        company = {
            "trailingPE": 10,
            "priceToBook": 1.0,
            "trailingEps": 5.0,
            "bookValue": 20.0,
            "marketCap": 50_000_000,
        }
        thresholds = {
            "max_pe": 15,
            "max_pb": 1.5,
            "max_debt_equity": 0.5,
            "min_roic_avg": 0.12,
            "min_positive_fcf_years": 8,
            "min_piotroski": 6,
        }

        fake_year_data = {
            "2024": {
                "net_income": 1_000_000,
                "depreciation": 100_000,
                "capex": 200_000,
                "operating_cash_flow": 1_200_000,
                "stockholders_equity": 5_000_000,
                "long_term_debt": 500_000,
                "current_assets": 3_000_000,
                "total_liabilities": 2_000_000,
                "revenue": 10_000_000,
                "shares_outstanding": 100_000,
            }
        }

        with patch(
            "analysis.quant_screen.screener._aggregate_financials_by_year",
            return_value=fake_year_data,
        ):
            result, passed = screen_company("TEST", company, fin_client, thresholds)

        assert "failed_criteria" in result

    def test_passing_company_has_empty_failed_criteria(self, monkeypatch):
        """A company that passes all criteria should have empty failed_criteria."""
        monkeypatch.setenv("ENVIRONMENT", "dev")

        fin_client = MagicMock()
        # Create 10 years of strong financials
        items = []
        for y in range(2015, 2025):
            items.append(
                {
                    "ticker": "GOOD",
                    "fiscal_year": str(y),
                    "net_income": 1_000_000,
                    "depreciation": 100_000,
                    "capex": 50_000,
                    "operating_cash_flow": 1_200_000,
                    "stockholders_equity": 5_000_000,
                    "long_term_debt": 100_000,
                    "current_assets": 3_000_000,
                    "total_liabilities": 1_000_000,
                    "revenue": 10_000_000,
                    "shares_outstanding": 100_000,
                }
            )
        fin_client.query.return_value = items

        company = {
            "trailingPE": 10,
            "priceToBook": 1.0,
            "trailingEps": 5.0,
            "bookValue": 20.0,
            "marketCap": 50_000_000,
        }
        thresholds = {
            "max_pe": 15,
            "max_pb": 1.5,
            "max_debt_equity": 0.5,
            "min_roic_avg": 0.12,
            "min_positive_fcf_years": 8,
            "min_piotroski": 6,
        }

        from analysis.quant_screen.screener import screen_company

        result, passed = screen_company("GOOD", company, fin_client, thresholds)
        if passed:
            assert result["failed_criteria"] == []

    def test_handler_does_not_duplicate_threshold_logic(self):
        """handler.py should NOT re-derive failed criteria — it should read from result."""
        import inspect

        from analysis.quant_screen import handler

        source = inspect.getsource(handler)
        # The handler should no longer have its own pe_max / pb_max / de_max derivation
        # (lines 75-80 in the original)
        assert "pe_max = float" not in source
        assert "pb_max = float" not in source


# ------------------------------------------------------------------ #
# Bug 3: Thesis generator reads mos_threshold from event               #
# ------------------------------------------------------------------ #


class TestThesisGeneratorMosThreshold:
    """_passes_all_stages should read mos_threshold from event with fallback."""

    def test_uses_event_mos_threshold_when_present(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")

        from analysis.thesis_generator.handler import _passes_all_stages

        event = {
            "quant_passed": True,
            "moat_score": 8,
            "management_score": 7,
            "margin_of_safety": 0.35,
            "mos_threshold": 0.40,  # IV handler stores this
        }
        passed, reason = _passes_all_stages(event)
        # MoS 0.35 <= threshold 0.40 → should fail
        assert passed is False
        assert "margin_of_safety" in reason

    def test_falls_back_to_default_030(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")

        from analysis.thesis_generator.handler import _passes_all_stages

        event = {
            "quant_passed": True,
            "moat_score": 8,
            "management_score": 7,
            "margin_of_safety": 0.35,
            # No mos_threshold in event → fallback to 0.30
        }
        passed, reason = _passes_all_stages(event)
        # MoS 0.35 > 0.30 default → should pass
        assert passed is True

    def test_hardcoded_mos_min_no_longer_used_for_comparison(self):
        """The module-level MOS_MIN constant should not be used in _passes_all_stages."""
        import inspect

        from analysis.thesis_generator import handler

        source = inspect.getsource(handler._passes_all_stages)
        # Should reference the event-derived threshold, not the module constant
        assert "MOS_MIN" not in source


# ------------------------------------------------------------------ #
# Bug 4: Moat handler should NOT store on error path                   #
# ------------------------------------------------------------------ #


class TestMoatHandlerErrorPath:
    """On LLM failure, moat handler should NOT write moat_score=0 to DynamoDB."""

    def test_no_store_on_llm_exception(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.moat_analysis.handler.LLMClient") as mock_llm_cls,
            patch("analysis.moat_analysis.handler.store_analysis_result") as mock_store,
            patch("analysis.moat_analysis.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.moat_analysis.handler.S3Client") as mock_s3_cls,
            patch("analysis.moat_analysis.handler.LessonsClient") as mock_lessons_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = RuntimeError("LLM call failed")
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = ""
            mock_lessons_cls.return_value = mock_lessons

            from analysis.moat_analysis.handler import handler

            with pytest.raises(RuntimeError, match="LLM call failed"):
                handler({"ticker": "AAPL", "metrics": {"sector": "Technology"}}, None)

        # store_analysis_result should NOT have been called on the error path
        mock_store.assert_not_called()

    def test_store_called_on_success(self, monkeypatch):
        """Sanity check: store IS called on success path."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        llm_response = {
            "content": {
                "moat_type": "narrow",
                "moat_sources": ["switching costs"],
                "moat_score": 6,
                "moat_trend": "stable",
                "pricing_power": 5,
                "customer_captivity": 6,
                "reasoning": "Decent moat.",
                "risks_to_moat": [],
                "confidence": 0.7,
            },
            "cost_usd": 0.01,
        }

        with (
            patch("analysis.moat_analysis.handler.LLMClient") as mock_llm_cls,
            patch("analysis.moat_analysis.handler.store_analysis_result") as mock_store,
            patch("analysis.moat_analysis.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.moat_analysis.handler.S3Client") as mock_s3_cls,
            patch("analysis.moat_analysis.handler.LessonsClient") as mock_lessons_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.return_value = llm_response
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = ""
            mock_lessons_cls.return_value = mock_lessons

            from analysis.moat_analysis.handler import handler

            handler({"ticker": "AAPL", "metrics": {"sector": "Technology"}}, None)

        mock_store.assert_called_once()
