"""Q-learning agent for the TradingEnv — trains on synthetic price data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backtesting.rl_env import TradingEnv

SEED = 42
N_EPISODES = 150
N_BINS = 10
ALPHA = 0.1  # learning rate
GAMMA = 0.99  # discount factor
EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY = 0.995


def generate_gbm_prices(
    n_days: int = 500,
    s0: float = 100.0,
    mu: float = 0.0005,
    sigma: float = 0.02,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate synthetic prices via geometric Brownian motion."""
    if rng is None:
        rng = np.random.default_rng(SEED)
    returns = rng.normal(mu, sigma, n_days)
    prices = s0 * np.exp(np.cumsum(returns))
    return prices


def _discretize(obs: np.ndarray, bins: int = N_BINS) -> tuple:
    """Convert continuous observation to discrete bin indices."""
    clipped = np.clip(obs, -2, 2)
    indices = np.floor((clipped + 2) / 4 * bins).astype(int)
    indices = np.clip(indices, 0, bins - 1)
    return tuple(indices.tolist())


def train(
    n_episodes: int = N_EPISODES,
    save_path: str | None = None,
    verbose: bool = True,
) -> dict:
    """Train a Q-learning agent on synthetic price data.

    Returns a dict with training_log and final_q_table_size.
    """
    rng = np.random.default_rng(SEED)
    prices = generate_gbm_prices(rng=rng)
    env = TradingEnv(prices, initial_capital=100_000.0)

    q_table: dict[tuple, np.ndarray] = {}
    epsilon = EPSILON_START
    training_log: list[dict] = []

    for ep in range(n_episodes):
        obs, _ = env.reset(seed=SEED + ep)
        state = _discretize(obs)
        total_reward = 0.0
        steps = 0

        while True:
            if state not in q_table:
                q_table[state] = np.zeros(env.action_space.n)

            if rng.random() < epsilon:
                action = int(env.action_space.sample())
            else:
                action = int(np.argmax(q_table[state]))

            next_obs, reward, terminated, truncated, info = env.step(action)
            next_state = _discretize(next_obs)

            if next_state not in q_table:
                q_table[next_state] = np.zeros(env.action_space.n)

            best_next = float(np.max(q_table[next_state]))
            q_table[state][action] += ALPHA * (reward + GAMMA * best_next - q_table[state][action])

            state = next_state
            total_reward += reward
            steps += 1

            if terminated or truncated:
                break

        epsilon = max(EPSILON_END, epsilon * EPSILON_DECAY)
        pv = info.get("portfolio_value", 0)

        entry = {
            "episode": ep,
            "total_reward": round(total_reward, 4),
            "portfolio_value": round(pv, 2),
            "trades": info.get("n_trades", 0),
            "epsilon": round(epsilon, 4),
        }
        training_log.append(entry)

        if verbose and ep % 25 == 0:
            print(
                f"Ep {ep:3d} | reward={total_reward:+.4f} | "
                f"PV=${pv:,.0f} | trades={info.get('n_trades', 0)} | "
                f"eps={epsilon:.3f}"
            )

    # Save Q-table
    if save_path is None:
        save_path = str(Path(__file__).parent / "q_table.json")

    serializable = {str(k): v.tolist() for k, v in q_table.items()}
    Path(save_path).write_text(json.dumps(serializable, indent=2))

    if verbose:
        print(f"\nQ-table saved to {save_path} ({len(q_table)} states)")

    return {
        "training_log": training_log,
        "q_table_size": len(q_table),
        "save_path": save_path,
    }


if __name__ == "__main__":
    result = train()
    final = result["training_log"][-1]
    print(
        f"\nFinal: PV=${final['portfolio_value']:,.0f}, "
        f"reward={final['total_reward']:+.4f}, "
        f"Q-states={result['q_table_size']}"
    )
