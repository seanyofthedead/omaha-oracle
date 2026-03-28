"""Tests for backtesting engine enhancements and RL environment."""

from __future__ import annotations

import tempfile

import numpy as np
import pytest

from backtesting.engine import _compute_enhanced_metrics
from backtesting.rl_env import TradingEnv
from backtesting.rl_train import generate_gbm_prices, train

# ── Enhanced metrics ─────────────────────────────────────────────────


class TestEnhancedMetrics:
    """Test Sharpe, Sortino, Calmar ratios and avg trade return."""

    def test_sharpe_positive_trend(self):
        """Steadily rising portfolio should have positive Sharpe."""
        values = [100 + i * 0.5 + (i % 3) * 0.1 for i in range(252)]
        sharpe, _, _, _ = _compute_enhanced_metrics(values, [], 5.0)
        assert sharpe > 0

    def test_sortino_ignores_upside(self):
        """Sortino should be >= Sharpe when there are positive outliers."""
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.01, 252)
        # Add some large positive outliers (upside vol)
        returns[::20] += 0.05
        values = [100.0]
        for r in returns:
            values.append(values[-1] * (1 + r))
        sharpe, sortino, _, _ = _compute_enhanced_metrics(values, [], 5.0)
        assert sortino >= sharpe

    def test_calmar_ratio(self):
        """Calmar = annualized_return / max_drawdown."""
        # Simple case: 10% total return over 252 days, 5% max drawdown
        values = [100 + i * (10 / 252) for i in range(253)]
        _, _, calmar, _ = _compute_enhanced_metrics(values, [], 5.0)
        assert calmar > 0

    def test_avg_trade_return(self):
        """Avg trade return from completed round-trips."""
        trades = [
            {"ticker": "AAPL", "signal": "BUY", "price": 100},
            {"ticker": "AAPL", "signal": "SELL", "price": 110},  # +10%
            {"ticker": "MSFT", "signal": "BUY", "price": 200},
            {"ticker": "MSFT", "signal": "SELL", "price": 180},  # -10%
        ]
        values = [100.0] * 10  # doesn't matter for trade return
        _, _, _, avg_ret = _compute_enhanced_metrics(values, trades, 1.0)
        assert avg_ret == pytest.approx(0.0, abs=0.01)

    def test_empty_values(self):
        """Empty portfolio values returns all zeros."""
        result = _compute_enhanced_metrics([], [], 0.0)
        assert result == (0.0, 0.0, 0.0, 0.0)

    def test_no_drawdown_calmar_zero(self):
        """Zero max drawdown means calmar is zero (avoid division by zero)."""
        values = [100.0 + i for i in range(100)]
        _, _, calmar, _ = _compute_enhanced_metrics(values, [], 0.0)
        assert calmar == 0.0


# ── RL Environment ───────────────────────────────────────────────────


class TestTradingEnv:
    """Test Gymnasium compatibility and basic behavior."""

    @pytest.fixture
    def env(self):
        rng = np.random.default_rng(42)
        prices = generate_gbm_prices(n_days=200, rng=rng)
        return TradingEnv(prices, initial_capital=100_000.0)

    def test_reset_returns_obs_info(self, env):
        obs, info = env.reset(seed=42)
        assert isinstance(obs, np.ndarray)
        assert isinstance(info, dict)
        assert obs.shape == env.observation_space.shape

    def test_step_returns_5_tuple(self, env):
        env.reset(seed=42)
        result = env.step(0)  # HOLD
        assert len(result) == 5
        obs, reward, terminated, truncated, info = result
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_action_space_sampling(self, env):
        for _ in range(100):
            action = env.action_space.sample()
            assert action in (0, 1, 2)

    def test_observation_in_space(self, env):
        obs, _ = env.reset(seed=42)
        assert env.observation_space.contains(obs)

    def test_buy_reduces_cash(self, env):
        env.reset(seed=42)
        cash_before = env.cash
        env.step(1)  # BUY
        assert env.cash < cash_before
        assert env.shares > 0

    def test_sell_after_buy(self, env):
        env.reset(seed=42)
        env.step(1)  # BUY
        assert env.shares > 0
        env.step(2)  # SELL
        assert env.shares == 0.0

    def test_sell_without_position_is_noop(self, env):
        env.reset(seed=42)
        cash_before = env.cash
        env.step(2)  # SELL with no position
        assert env.cash == cash_before

    def test_episode_terminates(self, env):
        env.reset(seed=42)
        terminated = False
        steps = 0
        while not terminated and steps < 500:
            _, _, terminated, truncated, _ = env.step(0)
            steps += 1
        assert terminated

    def test_with_features(self):
        rng = np.random.default_rng(42)
        prices = generate_gbm_prices(n_days=100, rng=rng)
        features = rng.standard_normal((100, 3)).astype(np.float32)
        env = TradingEnv(prices, features=features)
        obs, _ = env.reset(seed=42)
        assert obs.shape == (9,)  # 6 base + 3 features
        assert env.observation_space.contains(obs)


# ── RL Training ──────────────────────────────────────────────────────


class TestRLTraining:
    """Test Q-learning training runs without error."""

    def test_train_completes(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            save_path = f.name
        result = train(n_episodes=10, save_path=save_path, verbose=False)
        assert result["q_table_size"] > 0
        assert len(result["training_log"]) == 10
        assert result["training_log"][-1]["episode"] == 9

    def test_gbm_prices(self):
        prices = generate_gbm_prices(n_days=100)
        assert len(prices) == 100
        assert all(p > 0 for p in prices)

    def test_reproducibility(self):
        p1 = generate_gbm_prices(n_days=50, rng=np.random.default_rng(42))
        p2 = generate_gbm_prices(n_days=50, rng=np.random.default_rng(42))
        np.testing.assert_array_equal(p1, p2)
