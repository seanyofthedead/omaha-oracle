"""Shared dataclasses for the Alpaca paper-trading integration.

These decouple every dashboard module from alpaca-py internals.  Other
modules import *these* types — never the raw SDK models.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AccountSummary:
    """Snapshot of the paper-trading account."""

    account_id: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    account_blocked: bool = False
    daytrade_count: int = 0
    currency: str = "USD"
    status: str = "ACTIVE"


@dataclass
class PositionInfo:
    """A single open position."""

    asset_id: str
    symbol: str
    qty: float
    side: str
    avg_entry_price: float
    current_price: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_plpc: float = 0.0
    change_today: float = 0.0


@dataclass
class OrderInfo:
    """A submitted order."""

    order_id: str
    symbol: str
    qty: float
    side: str
    order_type: str
    time_in_force: str
    status: str
    created_at: str = ""
    filled_at: str | None = None
    filled_avg_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None


@dataclass
class AssetInfo:
    """Basic information about a tradable asset."""

    asset_id: str
    symbol: str
    name: str = ""
    asset_class: str = "us_equity"
    exchange: str = ""
    tradable: bool = False
    fractionable: bool = False
    status: str = "active"


@dataclass
class WatchlistInfo:
    """A named watchlist with its symbols."""

    watchlist_id: str
    name: str
    created_at: str = ""
    updated_at: str = ""
    symbols: list[str] = field(default_factory=list)
