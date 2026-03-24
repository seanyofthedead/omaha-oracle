"""Backtesting engine — replays historical decisions against actual prices."""

from __future__ import annotations

from typing import Any

import yfinance as yf


def run_backtest(
    decisions: list[dict[str, Any]],
    initial_capital: float = 100_000.0,
    max_position_pct: float = 0.15,
) -> dict[str, Any]:
    """Simulate portfolio performance from historical decisions.

    Parameters
    ----------
    decisions : list of decision dicts with keys: ticker, signal (BUY/SELL), timestamp
    initial_capital : starting cash
    max_position_pct : max fraction per position (Half-Kelly cap)

    Returns
    -------
    dict with keys: dates, portfolio_values, spy_values, trades, metrics
    """
    if not decisions:
        return _empty_result()

    # Sort decisions by timestamp
    sorted_decisions = sorted(decisions, key=lambda d: d.get("timestamp", ""))

    # Filter to BUY and SELL only
    trade_decisions = [
        d
        for d in sorted_decisions
        if d.get("signal", "").upper() in ("BUY", "SELL")
    ]

    if not trade_decisions:
        return _empty_result()

    start_date = trade_decisions[0]["timestamp"][:10]

    # Collect all tickers we need prices for
    tickers = list({d["ticker"] for d in trade_decisions if d.get("ticker")})
    tickers_with_spy = tickers + ["SPY"]

    # Fetch all price data in batch
    try:
        prices = yf.download(
            tickers_with_spy,
            start=start_date,
            progress=False,
            auto_adjust=True,
        )
        if prices.empty:
            return _empty_result()
    except Exception:
        return _empty_result()

    # Extract close prices
    if len(tickers_with_spy) == 1:
        close_prices = prices[["Close"]].copy()
        close_prices.columns = [tickers_with_spy[0]]
    else:
        close_prices = (
            prices["Close"]
            if "Close" in prices.columns.get_level_values(0)
            else prices
        )

    dates = [d.strftime("%Y-%m-%d") for d in close_prices.index]

    # Get SPY values normalized to initial_capital
    if "SPY" in close_prices.columns:
        spy_series = close_prices["SPY"].dropna()
        spy_values = (spy_series / spy_series.iloc[0] * initial_capital).tolist()
    else:
        spy_values = [initial_capital] * len(dates)

    # Simulate portfolio
    cash = initial_capital
    positions: dict[str, dict] = {}  # ticker -> {shares, entry_price}
    portfolio_values = []
    trades = []

    decision_map: dict[str, list] = {}
    for d in trade_decisions:
        date_key = d["timestamp"][:10]
        decision_map.setdefault(date_key, []).append(d)

    for i, date_str in enumerate(dates):
        # Execute any decisions for this date
        if date_str in decision_map:
            for dec in decision_map[date_str]:
                ticker = dec.get("ticker", "")
                signal = dec.get("signal", "").upper()

                if ticker not in close_prices.columns:
                    continue

                price = close_prices[ticker].iloc[i]
                if price is None or price != price:  # NaN check
                    continue
                price = float(price)

                if signal == "BUY" and ticker not in positions:
                    # Position size: max_position_pct of current portfolio value
                    current_value = cash + sum(
                        pos["shares"] * float(close_prices[t].iloc[i])
                        for t, pos in positions.items()
                        if t in close_prices.columns
                        and close_prices[t].iloc[i] == close_prices[t].iloc[i]
                    )
                    allocation = min(cash, current_value * max_position_pct)
                    if allocation > 0 and price > 0:
                        shares = allocation / price
                        positions[ticker] = {
                            "shares": shares,
                            "entry_price": price,
                        }
                        cash -= allocation
                        trades.append(
                            {
                                "date": date_str,
                                "ticker": ticker,
                                "signal": "BUY",
                                "price": price,
                                "shares": round(shares, 2),
                            }
                        )

                elif signal == "SELL" and ticker in positions:
                    pos = positions.pop(ticker)
                    proceeds = pos["shares"] * price
                    cash += proceeds
                    trades.append(
                        {
                            "date": date_str,
                            "ticker": ticker,
                            "signal": "SELL",
                            "price": price,
                            "shares": round(pos["shares"], 2),
                        }
                    )

        # Calculate total portfolio value
        total_value = cash
        for t, pos in positions.items():
            if t in close_prices.columns:
                p = close_prices[t].iloc[i]
                if p == p:  # not NaN
                    total_value += pos["shares"] * float(p)
        portfolio_values.append(total_value)

    # Calculate metrics
    if portfolio_values and len(portfolio_values) > 1:
        total_return = (portfolio_values[-1] / portfolio_values[0] - 1) * 100
        spy_return = (
            (spy_values[-1] / spy_values[0] - 1) * 100 if spy_values else 0
        )

        # Max drawdown
        peak = portfolio_values[0]
        max_dd = 0
        for v in portfolio_values:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100
            max_dd = max(max_dd, dd)

        # Win rate from trades
        completed_trades = {}
        for t in trades:
            ticker = t["ticker"]
            if t["signal"] == "BUY":
                completed_trades[ticker] = t["price"]
            elif t["signal"] == "SELL" and ticker in completed_trades:
                completed_trades[ticker] = t["price"] - completed_trades[ticker]

        wins = sum(1 for v in completed_trades.values() if v > 0)
        total_closed = len([t for t in trades if t["signal"] == "SELL"])
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    else:
        total_return = 0
        spy_return = 0
        max_dd = 0
        win_rate = 0

    return {
        "dates": dates,
        "portfolio_values": portfolio_values,
        "spy_values": spy_values[: len(dates)],
        "trades": trades,
        "metrics": {
            "total_return_pct": round(total_return, 2),
            "spy_return_pct": round(spy_return, 2),
            "alpha_pct": round(total_return - spy_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "win_rate_pct": round(win_rate, 1),
            "total_trades": len(trades),
            "open_positions": len(positions),
        },
    }


def _empty_result() -> dict[str, Any]:
    return {
        "dates": [],
        "portfolio_values": [],
        "spy_values": [],
        "trades": [],
        "metrics": {
            "total_return_pct": 0,
            "spy_return_pct": 0,
            "alpha_pct": 0,
            "max_drawdown_pct": 0,
            "win_rate_pct": 0,
            "total_trades": 0,
            "open_positions": 0,
        },
    }
