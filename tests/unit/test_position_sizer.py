"""
Unit tests for position sizing via Kelly criterion.

- Positive Kelly → correct size
- Negative Kelly → zero size
- Max position cap enforcement
- Min position threshold
"""

from __future__ import annotations

import pytest

from portfolio.allocation.position_sizer import calculate_position_size


class TestPositiveKelly:
    """Positive Kelly fraction produces correct size."""

    def test_favourable_odds_produces_position(self):
        # p=0.6, b=2 → Kelly = (2*0.6 - 0.4) / 4 = 0.2, Half-Kelly = 0.1
        result = calculate_position_size(
            win_probability=0.6,
            win_loss_ratio=2.0,
            portfolio_value=100_000,
            current_positions=[],
        )
        assert result["can_buy"] is True
        assert result["position_size_usd"] >= 2000
        assert result["kelly_fraction"] > 0
        assert result["position_pct"] <= 0.15

    def test_strong_edge_large_position(self):
        # p=0.7, b=3 → Kelly raw high
        result = calculate_position_size(
            win_probability=0.7,
            win_loss_ratio=3.0,
            portfolio_value=100_000,
            current_positions=[],
        )
        assert result["can_buy"] is True
        assert result["position_size_usd"] == pytest.approx(15_000, rel=0.01)  # 15% cap


class TestNegativeKelly:
    """Negative Kelly → zero size, can_buy=False."""

    def test_unfavourable_odds_zero_size(self):
        result = calculate_position_size(
            win_probability=0.3,
            win_loss_ratio=1.0,
            portfolio_value=100_000,
            current_positions=[],
        )
        assert result["can_buy"] is False
        assert result["kelly_fraction"] == 0.0
        assert "Kelly fraction ≤ 0" in result["reasoning"]

    def test_breakeven_odds_zero_size(self):
        result = calculate_position_size(
            win_probability=0.5,
            win_loss_ratio=1.0,
            portfolio_value=100_000,
            current_positions=[],
        )
        assert result["can_buy"] is False


class TestMaxPositionCap:
    """Max position cap (15%) enforced."""

    def test_position_capped_at_max_pct(self):
        result = calculate_position_size(
            win_probability=0.9,
            win_loss_ratio=5.0,
            portfolio_value=100_000,
            current_positions=[],
            max_position_pct=0.15,
        )
        assert result["position_pct"] <= 0.15
        assert result["position_size_usd"] == 15_000

    def test_custom_max_respected(self):
        result = calculate_position_size(
            win_probability=0.8,
            win_loss_ratio=4.0,
            portfolio_value=100_000,
            current_positions=[],
            max_position_pct=0.10,
        )
        assert result["position_pct"] <= 0.10
        assert result["position_size_usd"] == 10_000


class TestMinPositionThreshold:
    """Min position USD (default 2000) enforced."""

    def test_small_portfolio_respects_min_when_under_cap(self):
        # Portfolio 20k: 15% cap = 3k, Kelly may suggest less; min 2k applies
        result = calculate_position_size(
            win_probability=0.6,
            win_loss_ratio=2.0,
            portfolio_value=20_000,
            current_positions=[],
            min_position_usd=2000,
        )
        assert result["position_size_usd"] >= 2000

    def test_tiny_portfolio_capped_by_max_pct(self):
        # Portfolio 5k: 15% = 750, so position cannot exceed 750 (min 2k overridden)
        result = calculate_position_size(
            win_probability=0.6,
            win_loss_ratio=2.0,
            portfolio_value=5_000,
            current_positions=[],
            min_position_usd=2000,
        )
        assert result["position_size_usd"] <= 750

    def test_custom_min_respected(self):
        result = calculate_position_size(
            win_probability=0.6,
            win_loss_ratio=2.0,
            portfolio_value=50_000,
            current_positions=[],
            min_position_usd=5000,
        )
        assert result["position_size_usd"] >= 5000


class TestMaxPositions:
    """At max positions, can_buy=False."""

    def test_at_max_positions_cannot_buy(self):
        positions = [{"ticker": f"T{i}"} for i in range(20)]
        result = calculate_position_size(
            win_probability=0.7,
            win_loss_ratio=2.0,
            portfolio_value=100_000,
            current_positions=positions,
            max_positions=20,
        )
        assert result["can_buy"] is False
        assert "max positions" in result["reasoning"].lower()
