"""Gymnasium-compatible reinforcement learning environment for stock trading."""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces


class TradingEnv(gym.Env):
    """Gymnasium environment for stock trading with value investing signals.

    Actions: 0=HOLD, 1=BUY, 2=SELL
    Observation: [cash_ratio, position_ratio, unrealized_pnl,
                  price_change_1d, price_change_5d, price_change_20d, ...features]
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        prices: np.ndarray,
        features: np.ndarray | None = None,
        initial_capital: float = 100_000.0,
        max_position_pct: float = 0.15,
        transaction_cost_pct: float = 0.001,
    ):
        super().__init__()
        self.prices = np.asarray(prices, dtype=np.float64)
        self.features = features
        self.initial_capital = initial_capital
        self.max_position_pct = max_position_pct
        self.transaction_cost_pct = transaction_cost_pct

        self.action_space = spaces.Discrete(3)

        n_base = 6
        n_extra = features.shape[1] if features is not None else 0
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(n_base + n_extra,), dtype=np.float32
        )

        self.current_step = 0
        self.cash = initial_capital
        self.shares = 0.0
        self.entry_price = 0.0
        self.portfolio_values: list[float] = []
        self.trades: list[dict[str, object]] = []

    def reset(
        self, seed: int | None = None, options: dict[str, object] | None = None,
    ) -> tuple[np.ndarray, dict[str, object]]:
        super().reset(seed=seed)
        self.current_step = 20  # need lookback for price changes
        self.cash = self.initial_capital
        self.shares = 0.0
        self.entry_price = 0.0
        self.portfolio_values = []
        self.trades = []
        return self._get_obs(), {}

    def _get_obs(self) -> np.ndarray:
        price = self.prices[self.current_step]
        pv = self.cash + self.shares * price
        cash_ratio = self.cash / pv if pv > 0 else 1.0
        pos_ratio = (self.shares * price) / pv if pv > 0 else 0.0
        unrealized = (
            (price - self.entry_price) / self.entry_price
            if self.shares > 0 and self.entry_price > 0
            else 0.0
        )

        idx = self.current_step
        p = self.prices
        c1 = (p[idx] / p[idx - 1] - 1) if p[idx - 1] > 0 else 0.0
        c5 = (p[idx] / p[max(idx - 5, 0)] - 1) if p[max(idx - 5, 0)] > 0 else 0.0
        c20 = (p[idx] / p[max(idx - 20, 0)] - 1) if p[max(idx - 20, 0)] > 0 else 0.0

        obs = np.array([cash_ratio, pos_ratio, unrealized, c1, c5, c20], dtype=np.float32)
        if self.features is not None:
            obs = np.concatenate([obs, self.features[self.current_step].astype(np.float32)])
        return obs

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, object]]:
        price = self.prices[self.current_step]
        pv_before = self.cash + self.shares * price
        reward = 0.0

        if action == 1 and self.shares == 0:  # BUY
            invest = min(self.cash, pv_before * self.max_position_pct)
            cost = invest * self.transaction_cost_pct
            if invest > cost and price > 0:
                self.shares = (invest - cost) / price
                self.cash -= invest
                self.entry_price = price
                self.trades.append(
                    {"step": self.current_step, "action": "BUY", "price": float(price)}
                )

        elif action == 2 and self.shares > 0:  # SELL
            proceeds = self.shares * price
            cost = proceeds * self.transaction_cost_pct
            self.cash += proceeds - cost
            if self.entry_price > 0:
                reward = (price - self.entry_price) / self.entry_price
            self.shares = 0.0
            self.entry_price = 0.0
            self.trades.append({"step": self.current_step, "action": "SELL", "price": float(price)})

        pv_after = self.cash + self.shares * price
        self.current_step += 1
        self.portfolio_values.append(pv_after)

        daily_ret = (pv_after / pv_before - 1) if pv_before > 0 else 0
        reward += daily_ret * 0.1

        terminated = self.current_step >= len(self.prices) - 1
        truncated = False
        info = {
            "portfolio_value": pv_after,
            "cash": self.cash,
            "shares": self.shares,
            "n_trades": len(self.trades),
        }
        return self._get_obs(), float(reward), terminated, truncated, info
