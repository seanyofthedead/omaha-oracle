"""
Tests for Piotroski F-Score calculator — verifies correct year ordering.

The caller (screener.py) passes years_sorted in DESCENDING order
(most recent first). These tests confirm that piotroski_score treats
index [0] as the current year and index [1] as the previous year.
"""

from __future__ import annotations

from analysis.quant_screen.piotroski import piotroski_score


def _make_year_data(
    net_income: float,
    total_assets: float,
    operating_cash_flow: float,
    long_term_debt: float,
    current_assets: float,
    current_liabilities: float,
    shares_outstanding: float,
    revenue: float,
    depreciation: float,
    capex: float,
    stockholders_equity: float = 0.0,
) -> dict[str, float]:
    return {
        "net_income": net_income,
        "total_assets": total_assets,
        "operating_cash_flow": operating_cash_flow,
        "long_term_debt": long_term_debt,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "shares_outstanding": shares_outstanding,
        "revenue": revenue,
        "depreciation": depreciation,
        "capex": capex,
        "stockholders_equity": stockholders_equity,
    }


# --- Improving company: 2024 is better than 2023 in every trend signal ---
IMPROVING_2023 = _make_year_data(
    net_income=80,
    total_assets=1000,
    operating_cash_flow=90,
    long_term_debt=300,
    current_assets=400,
    current_liabilities=300,
    shares_outstanding=100,
    revenue=800,
    depreciation=50,
    capex=60,
)

IMPROVING_2024 = _make_year_data(
    net_income=120,        # higher NI -> better ROA, ROA change positive
    total_assets=1000,
    operating_cash_flow=150,  # higher OCF, and OCF > NI
    long_term_debt=200,    # lower debt -> leverage decreased
    current_assets=500,    # higher CA -> better current ratio
    current_liabilities=300,
    shares_outstanding=100,  # same shares -> no dilution
    revenue=1000,          # higher revenue -> better margin & turnover
    depreciation=50,
    capex=60,
)

# --- Deteriorating company: 2024 is worse than 2023 in every trend signal ---
DETERIORATING_2023 = _make_year_data(
    net_income=120,
    total_assets=1000,
    operating_cash_flow=150,
    long_term_debt=200,
    current_assets=500,
    current_liabilities=300,
    shares_outstanding=100,
    revenue=1000,
    depreciation=50,
    capex=60,
)

DETERIORATING_2024 = _make_year_data(
    net_income=50,          # lower NI -> worse ROA, ROA change negative
    total_assets=1000,
    operating_cash_flow=40,   # lower OCF, and OCF < NI
    long_term_debt=400,     # higher debt -> leverage increased
    current_assets=300,     # lower CA -> worse current ratio
    current_liabilities=350,
    shares_outstanding=120,  # more shares -> dilution
    revenue=700,            # lower revenue -> worse margin & turnover
    depreciation=50,
    capex=60,
)


class TestPiotroskiYearOrdering:
    """Verify that years_sorted[0] is treated as 'current' (most recent)."""

    def test_improving_company_scores_high(self):
        """An improving company (2024 better than 2023) should score high."""
        by_year = {2024: IMPROVING_2024, 2023: IMPROVING_2023}
        # Caller passes descending order
        years_sorted = [2024, 2023]

        score = piotroski_score(by_year, years_sorted)

        # With the bug (years inverted), trend signals are backwards,
        # so the improving company would score LOW on trend signals.
        # Correct behavior: all 9 criteria should pass.
        assert score >= 7, (
            f"Improving company should score >= 7, got {score}. "
            "Year ordering may be inverted."
        )

    def test_deteriorating_company_scores_low(self):
        """A deteriorating company (2024 worse than 2023) should score low."""
        by_year = {2024: DETERIORATING_2024, 2023: DETERIORATING_2023}
        years_sorted = [2024, 2023]

        score = piotroski_score(by_year, years_sorted)

        # With the bug, trend signals flip and the deteriorating company
        # would incorrectly score HIGH. Correct: should score low.
        assert score <= 3, (
            f"Deteriorating company should score <= 3, got {score}. "
            "Year ordering may be inverted."
        )

    def test_current_year_is_index_zero(self):
        """Directly verify that index 0 of years_sorted is used as current year."""
        # Make 2024 have positive ROA and 2023 have zero ROA.
        # If years are inverted, ROA check would use 2023 (zero) as current.
        by_year = {
            2024: _make_year_data(
                net_income=100, total_assets=1000,
                operating_cash_flow=120, long_term_debt=100,
                current_assets=500, current_liabilities=200,
                shares_outstanding=100, revenue=1000,
                depreciation=50, capex=40,
            ),
            2023: _make_year_data(
                net_income=0, total_assets=1000,
                operating_cash_flow=0, long_term_debt=100,
                current_assets=500, current_liabilities=200,
                shares_outstanding=100, revenue=1000,
                depreciation=50, capex=40,
            ),
        }
        years_sorted = [2024, 2023]

        score = piotroski_score(by_year, years_sorted)

        # Signal 1 (ROA > 0): should pass because 2024 NI=100, TA=1000
        # Signal 2 (OCF > 0): should pass because 2024 OCF=120
        # With the bug, both would fail since 2023 has NI=0, OCF=0
        assert score >= 2, (
            f"Expected score >= 2 (ROA>0 and OCF>0 for 2024), got {score}. "
            "The function may be using the wrong year as 'current'."
        )

    def test_three_year_descending_list(self):
        """With 3 years descending, should use [0] as current and [1] as previous."""
        by_year = {
            2024: IMPROVING_2024,
            2023: IMPROVING_2023,
            2022: _make_year_data(
                net_income=50, total_assets=900,
                operating_cash_flow=60, long_term_debt=350,
                current_assets=350, current_liabilities=300,
                shares_outstanding=100, revenue=700,
                depreciation=40, capex=50,
            ),
        }
        years_sorted = [2024, 2023, 2022]

        score = piotroski_score(by_year, years_sorted)

        # Should compare 2024 vs 2023 (not 2022 vs 2023)
        assert score >= 7, (
            f"With 3 years, should compare 2024 vs 2023 and score >= 7, got {score}."
        )
