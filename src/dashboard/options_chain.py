"""Options chain filtering and formatting logic.

Pure functions — no Streamlit or API calls. Operates on
``OptionContractInfo`` dataclasses returned by ``AlpacaClient``.
"""

from __future__ import annotations

import pandas as pd

from dashboard.alpaca_models import OptionContractInfo


def filter_contracts(
    contracts: list[OptionContractInfo],
    *,
    contract_type: str | None = None,
    expiration_date: str | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
) -> list[OptionContractInfo]:
    """Filter a list of option contracts by type, expiration, and strike range."""
    result = contracts
    if contract_type:
        result = [c for c in result if c.contract_type == contract_type]
    if expiration_date:
        result = [c for c in result if c.expiration_date == expiration_date]
    if strike_min is not None:
        result = [c for c in result if c.strike_price >= strike_min]
    if strike_max is not None:
        result = [c for c in result if c.strike_price <= strike_max]
    return result


def get_expirations(contracts: list[OptionContractInfo]) -> list[str]:
    """Return sorted unique expiration dates from the contract list."""
    return sorted({c.expiration_date for c in contracts})


def get_strikes(contracts: list[OptionContractInfo]) -> list[float]:
    """Return sorted unique strike prices from the contract list."""
    return sorted({c.strike_price for c in contracts})


def contracts_to_dataframe(contracts: list[OptionContractInfo]) -> pd.DataFrame:
    """Convert contracts to a display-ready DataFrame."""
    if not contracts:
        return pd.DataFrame()
    rows = [
        {
            "Symbol": c.symbol,
            "Type": c.contract_type.upper(),
            "Expiration": c.expiration_date,
            "Strike": c.strike_price,
            "Last Price": c.close_price,
            "Open Interest": c.open_interest,
            "Tradable": c.tradable,
        }
        for c in contracts
    ]
    return pd.DataFrame(rows)
