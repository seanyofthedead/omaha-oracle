"""
Unit tests for quant screen metric calculations.

- Apple: should pass most screens (quality company)
- GameStop: should fail (negative earnings, volatile)
- Edge cases: zero equity, negative revenue, missing data
"""

from __future__ import annotations

from unittest.mock import MagicMock

from analysis.quant_screen.financials import (
    DEFAULT_THRESHOLDS,
    _aggregate_financials_by_year,
    _cv,
)
from analysis.quant_screen.piotroski import piotroski_score
from analysis.quant_screen.screener import screen_company
from tests.fixtures.mock_data import (
    APPLE_COMPANY_QUANT_PASS,
    APPLE_FINANCIAL_ITEMS,
    APPLE_FINANCIALS_BY_YEAR,
    GAMESTOP_COMPANY,
    GAMESTOP_FINANCIAL_ITEMS,
    NEGATIVE_REVENUE_COMPANY,
    NEGATIVE_REVENUE_FINANCIALS,
    ZERO_EQUITY_COMPANY,
    ZERO_EQUITY_FINANCIALS,
)


def _by_year_from_items(items: list) -> dict:
    return _aggregate_financials_by_year(items)


def _make_fin_client(items: list) -> MagicMock:
    """Return a mock DynamoClient whose .query() returns items."""
    client = MagicMock()
    client.query.return_value = items
    return client


class TestAggregateFinancialsByYear:
    """Test _aggregate_financials_by_year."""

    def test_groups_by_year(self):
        by_year = _aggregate_financials_by_year(APPLE_FINANCIAL_ITEMS)
        assert 2024 in by_year
        assert 2015 in by_year
        assert by_year[2024]["revenue"] > 0
        assert by_year[2024]["net_income"] > 0

    def test_handles_period_with_hash(self):
        items = [{"period_end_date": "2024-09-30#Q4", "metric_name": "revenue", "value": 100}]
        by_year = _aggregate_financials_by_year(items)
        assert 2024 in by_year
        assert by_year[2024]["revenue"] == 100


class TestCV:
    """Test coefficient of variation."""

    def test_empty_returns_zero(self):
        assert _cv([]) == 0.0

    def test_zero_mean_returns_zero(self):
        assert _cv([0, 0, 0]) == 0.0

    def test_stable_values_low_cv(self):
        assert _cv([10, 10, 10]) == 0.0

    def test_volatile_values_high_cv(self):
        cv = _cv([1, 10, 100])
        assert cv > 0.5


class TestPiotroskiScore:
    """Test piotroski_score."""

    def test_apple_scores_high(self):
        years = sorted(APPLE_FINANCIALS_BY_YEAR.keys(), reverse=True)
        score = piotroski_score(APPLE_FINANCIALS_BY_YEAR, years)
        assert score >= 6

    def test_less_than_two_years_returns_zero(self):
        assert piotroski_score({2024: {}}, [2024]) == 0


class TestScreenCompanyApple:
    """Apple should pass most screens."""

    def test_apple_passes_with_conservative_multiples(self):
        result, passed = screen_company(
            "AAPL",
            APPLE_COMPANY_QUANT_PASS,
            _make_fin_client(APPLE_FINANCIAL_ITEMS),
            DEFAULT_THRESHOLDS,
        )
        assert passed is True
        assert result["pass"] is True
        assert result["pe"] < 15
        assert result["pb"] < 1.5
        assert result["roic_10y_avg"] >= 0.12
        assert result["positive_fcf_years"] >= 8
        assert result["piotroski_score"] >= 6


class TestScreenCompanyGameStop:
    """GameStop should fail (negative earnings, volatile)."""

    def test_gamestop_fails(self):
        result, passed = screen_company(
            "GME",
            GAMESTOP_COMPANY,
            _make_fin_client(GAMESTOP_FINANCIAL_ITEMS),
            DEFAULT_THRESHOLDS,
        )
        assert passed is False
        assert result["pass"] is False

    def test_gamestop_negative_pe_or_low_piotroski(self):
        result, _ = screen_company(
            "GME",
            GAMESTOP_COMPANY,
            _make_fin_client(GAMESTOP_FINANCIAL_ITEMS),
            DEFAULT_THRESHOLDS,
        )
        # Either negative earnings, high volatility, or low Piotroski
        assert (
            result["pe"] <= 0
            or result["positive_fcf_years"] < 8
            or result["piotroski_score"] < 6
            or result["earnings_cv"] > 0.5
        )


class TestScreenCompanyEdgeCases:
    """Edge cases: zero equity, negative revenue, missing data."""

    def test_zero_equity_fails_debt_ratio(self):
        items = []
        for year, metrics in ZERO_EQUITY_FINANCIALS.items():
            for m, v in metrics.items():
                items.append({"period_end_date": f"{year}-12-31", "metric_name": m, "value": v})
        result, passed = screen_company(
            "ZERO",
            ZERO_EQUITY_COMPANY,
            _make_fin_client(items),
            DEFAULT_THRESHOLDS,
        )
        assert passed is False
        assert result["debt_equity"] >= 0.5 or result.get("pass") is False

    def test_negative_revenue_fails(self):
        items = []
        for year, metrics in NEGATIVE_REVENUE_FINANCIALS.items():
            for m, v in metrics.items():
                items.append({"period_end_date": f"{year}-12-31", "metric_name": m, "value": v})
        result, passed = screen_company(
            "NEG",
            NEGATIVE_REVENUE_COMPANY,
            _make_fin_client(items),
            DEFAULT_THRESHOLDS,
        )
        assert passed is False

    def test_no_financials_returns_no_pass(self):
        result, passed = screen_company(
            "MISSING",
            {"ticker": "MISSING", "trailingPE": 10, "marketCap": 1e9},
            _make_fin_client([]),
            DEFAULT_THRESHOLDS,
        )
        assert passed is False
        assert result.get("reason") == "no_financials" or not result.get("pass")
