"""
Unit tests for intrinsic value estimation.

Coverage:
  - _dcf_pv: pure-math DCF present value
  - _extract_inputs: flexible metric key resolution
  - handler: composite valuation, MoS, buy signal, config-table threshold
"""

from __future__ import annotations

import pytest

from analysis.intrinsic_value.handler import (
    DCF_WEIGHT,
    DEFAULT_MOS_THRESHOLD,
    EPV_WEIGHT,
    FLOOR_WEIGHT,
    _dcf_pv,
    _extract_inputs,
    handler,
)
from tests.conftest import TABLE_CONFIG

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #


def _base_event(
    owner_earnings: float = 100_000.0,
    shares: float = 100.0,
    price: float = 500.0,
    current_assets: float = 2_000_000.0,
    total_liabilities: float = 500_000.0,
) -> dict:
    return {
        "ticker": "AAPL",
        "metrics": {
            "owner_earnings": owner_earnings,
            "current_assets": current_assets,
            "total_liabilities": total_liabilities,
            "shares_outstanding": shares,
            "current_price": price,
            "market_cap": shares * price,
        },
    }


# ------------------------------------------------------------------ #
# TestDcfPv                                                            #
# ------------------------------------------------------------------ #


class TestDcfPv:
    """_dcf_pv pure-math tests — no mocks needed."""

    def test_positive_earnings_produces_positive_pv(self):
        assert _dcf_pv(100_000, 0.06) > 0

    def test_zero_fcf_returns_zero(self):
        assert _dcf_pv(0, 0.06) == 0.0

    def test_negative_fcf_returns_zero(self):
        assert _dcf_pv(-1_000, 0.06) == 0.0

    def test_higher_growth_produces_higher_pv(self):
        bear = _dcf_pv(100_000, 0.02)
        base = _dcf_pv(100_000, 0.06)
        bull = _dcf_pv(100_000, 0.10)
        assert bear < base < bull

    def test_terminal_value_dominates_explicit_years(self):
        """For a typical long-lived business the terminal value exceeds
        the sum of the 10 explicit years' cash flows."""
        from analysis.intrinsic_value.handler import DISCOUNT_RATE, TERMINAL_GROWTH, YEARS

        fcf0 = 100_000.0
        growth = 0.06
        r = DISCOUNT_RATE
        g_term = TERMINAL_GROWTH

        pv_explicit = sum(fcf0 * ((1 + growth) ** t) / ((1 + r) ** t) for t in range(1, YEARS + 1))
        fcf_10 = fcf0 * ((1 + growth) ** YEARS)
        terminal_value = fcf_10 * (1 + g_term) / (r - g_term)
        pv_terminal = terminal_value / ((1 + r) ** YEARS)

        assert pv_terminal > pv_explicit


# ------------------------------------------------------------------ #
# TestExtractInputs                                                    #
# ------------------------------------------------------------------ #


class TestExtractInputs:
    """_extract_inputs flexible key resolution."""

    def test_owner_earnings_direct(self):
        inputs = _extract_inputs(
            {"owner_earnings": 50_000.0, "shares_outstanding": 100.0, "current_price": 10.0}
        )
        assert inputs["owner_earnings"] == 50_000.0

    def test_owner_earnings_calculated_from_ni_dep_capex(self):
        inputs = _extract_inputs(
            {
                "net_income": 50_000.0,
                "depreciation": 10_000.0,
                "capex": 20_000.0,
                "shares_outstanding": 100.0,
                "current_price": 10.0,
            }
        )
        # owner_earnings = ni + dep - 0.7 * capex = 50_000 + 10_000 - 14_000 = 46_000
        assert inputs["owner_earnings"] == pytest.approx(46_000.0)

    def test_shares_derived_from_mcap_price(self):
        inputs = _extract_inputs(
            {
                "owner_earnings": 1_000.0,
                "shares_outstanding": 0.0,
                "market_cap": 200_000.0,
                "current_price": 100.0,
            }
        )
        assert inputs["shares"] == pytest.approx(2_000.0)

    def test_nnwc_derived_from_ca_minus_tl(self):
        inputs = _extract_inputs(
            {
                "owner_earnings": 1_000.0,
                "current_assets": 500_000.0,
                "total_liabilities": 200_000.0,
                "shares_outstanding": 100.0,
                "current_price": 10.0,
            }
        )
        assert inputs["nnwc"] == pytest.approx(300_000.0)

    def test_all_zero_inputs_returns_zeros(self):
        inputs = _extract_inputs({})
        assert inputs["owner_earnings"] == 0.0
        assert inputs["shares"] == 0.0
        assert inputs["price"] == 0.0


# ------------------------------------------------------------------ #
# TestCompositeValuationAndMos                                         #
# ------------------------------------------------------------------ #


class TestCompositeValuationAndMos:
    """Handler-level tests — require the iv_tables moto fixture."""

    def test_buy_signal_true_when_mos_above_threshold(self, iv_tables):
        # owner_earnings=100_000, shares=100, price=500
        # EPV per share = 1_000_000/100 = 10_000 >> price → MoS ≈ 0.95 > 0.30
        result = handler(_base_event(), None)
        assert result["buy_signal"] is True
        assert result["margin_of_safety"] > DEFAULT_MOS_THRESHOLD

    def test_buy_signal_false_when_overpriced(self, iv_tables):
        # price=100_000_000 far exceeds composite → MoS negative → no buy
        result = handler(_base_event(price=100_000_000.0), None)
        assert result["buy_signal"] is False

    def test_no_shares_returns_error_key(self, iv_tables):
        event = {
            "ticker": "ZERO",
            "metrics": {
                "owner_earnings": 50_000.0,
                "shares_outstanding": 0.0,
                "current_price": 100.0,
                "market_cap": 0.0,
            },
        }
        result = handler(event, None)
        assert result.get("error") == "no_shares_outstanding"
        assert result["buy_signal"] is False

    def test_dcf_epv_floor_weights_sum_correctly(self, iv_tables):
        result = handler(_base_event(), None)
        composite = result["intrinsic_value_per_share"]
        expected = (
            DCF_WEIGHT * result["dcf_per_share"]
            + EPV_WEIGHT * result["epv_per_share"]
            + FLOOR_WEIGHT * result["floor_per_share"]
        )
        assert composite == pytest.approx(expected, rel=1e-6)

    def test_mos_threshold_loaded_from_config_table(self, iv_tables):
        # Seed config table with a custom MOS threshold of 0.40
        iv_tables.Table(TABLE_CONFIG).put_item(
            Item={
                "config_key": "intrinsic_value",
                "value": {"margin_of_safety_threshold": "0.40"},
            }
        )
        result = handler(_base_event(), None)
        assert result["mos_threshold"] == pytest.approx(0.40)
