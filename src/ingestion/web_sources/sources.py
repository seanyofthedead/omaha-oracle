"""Concrete web source definitions for stock candidate discovery.

Each source is a ``SchemaWebSource`` subclass that only needs to set class
attributes.  Firecrawl's LLM extraction handles the parsing — no custom
HTML/regex parsers required.

Revised source list (all verified scrapable):
  1. Finviz Value Screener          (daily, stealth proxy)
  2. Finviz Insider Buying          (daily)
  3. SEC EDGAR Full-Text Search     (daily, custom User-Agent)
  4. Dataroma Guru Holdings         (weekly, replaces GuruFocus)
  5. Finviz Analyst Upgrades        (daily, replaces MarketBeat)
  6. Barchart Volume Leaders        (daily, JS rendering)
  7. Finviz Oversold / 52-Week Low  (daily, stealth proxy)
  8. Finviz High Dividend Yield     (weekly, replaces Dividend.com)
  9. SEC 13F Institutional Filings  (weekly, custom User-Agent)
 10. Yahoo Finance Earnings Calendar(weekly, replaces Earnings Whispers)
 11. Finviz Undervalued Growth      (weekly, replaces Simply Wall St)
 12. StockAnalysis Insider Trading  (daily, JS rendering)
"""

from __future__ import annotations

from typing import Any

from .base import SchemaWebSource
from .registry import SourceRegistry

# Shared SEC EDGAR User-Agent (required by fair-use policy)
_SEC_HEADERS = {"User-Agent": "OmahaOracle/1.0 contact@omaha-oracle.com"}


# ---------------------------------------------------------------------------
# 1. Finviz Value Screener
# ---------------------------------------------------------------------------


class FinvizValueScreener(SchemaWebSource):
    name = "finviz_value"
    signal_type = "value_screen"
    frequency = "daily"
    url = "https://finviz.com/screener.ashx?v=111&f=fa_pe_u15,fa_pb_u2,fa_roe_o10,cap_midover"
    extraction_prompt = (
        "Extract all stock tickers and their financial metrics from the screener "
        "table. Include ticker symbol, company name, sector, market cap, P/E ratio, "
        "price, and volume for each row."
    )
    proxy = "stealth"
    default_confidence = 0.6


# ---------------------------------------------------------------------------
# 2. Finviz Insider Buying
# ---------------------------------------------------------------------------


class FinvizInsiderBuying(SchemaWebSource):
    name = "finviz_insider"
    signal_type = "insider_buy"
    frequency = "daily"
    url = "https://finviz.com/insidertrading.ashx?tc=1"
    extraction_prompt = (
        "Extract all stock tickers from the insider trading table where the "
        "transaction type is a Purchase (P). Include ticker, insider name, "
        "relationship, transaction value, and date."
    )
    default_confidence = 0.7

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "insider_name": {"type": "string"},
                            "relationship": {"type": "string"},
                            "transaction_value": {"type": "number"},
                            "transaction_date": {"type": "string"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 3. SEC EDGAR Full-Text Search
# ---------------------------------------------------------------------------


class SECEdgarFullText(SchemaWebSource):
    name = "sec_fulltext"
    signal_type = "filing_mention"
    frequency = "daily"
    url = (
        "https://efts.sec.gov/LATEST/search-index?"
        "q=%22undervalued%22+OR+%22margin+of+safety%22+OR+%22intrinsic+value%22"
        "&dateRange=custom&startdt={start_date}&enddt={end_date}"
        "&forms=10-K,10-Q,8-K,SC+13D"
    )
    headers = _SEC_HEADERS
    extraction_prompt = (
        "Extract all company ticker symbols mentioned in these SEC filing "
        "search results. Include the filing type, date, and a brief excerpt "
        "of the relevant text mentioning undervaluation or intrinsic value."
    )
    default_confidence = 0.5

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "filing_type": {"type": "string"},
                            "filing_date": {"type": "string"},
                            "excerpt": {"type": "string"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 4. Dataroma Guru Holdings (replaces GuruFocus)
# ---------------------------------------------------------------------------


class DataromaGurus(SchemaWebSource):
    name = "dataroma_gurus"
    signal_type = "guru_holding"
    frequency = "weekly"
    url = "https://www.dataroma.com/m/home.php"
    extraction_prompt = (
        "Extract all stock tickers and portfolio information from the superinvestor "
        "holdings table. Include ticker symbol, company name, number of gurus "
        "holding the stock, and the aggregate portfolio percentage."
    )
    default_confidence = 0.65

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "num_gurus": {"type": "integer"},
                            "portfolio_pct": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 5. Finviz Analyst Upgrades (replaces MarketBeat)
# ---------------------------------------------------------------------------


class FinvizAnalystUpgrades(SchemaWebSource):
    name = "finviz_analyst"
    signal_type = "analyst_upgrade"
    frequency = "daily"
    url = "https://finviz.com/screener.ashx?v=111&f=an_recom_buybetter,cap_midover"
    extraction_prompt = (
        "Extract all stock tickers from the screener table that have analyst "
        "recommendations of Buy or better. Include ticker, company name, sector, "
        "market cap, P/E ratio, and price."
    )
    proxy = "stealth"
    default_confidence = 0.55


# ---------------------------------------------------------------------------
# 6. Barchart Volume Leaders
# ---------------------------------------------------------------------------


class BarchartVolumeLeaders(SchemaWebSource):
    name = "barchart_volume"
    signal_type = "unusual_volume"
    frequency = "daily"
    url = "https://www.barchart.com/stocks/most-active/daily-volume-leaders"
    wait_for = 5000
    extraction_prompt = (
        "Extract all stock tickers from the daily volume leaders table. "
        "Include ticker symbol, company name, price, volume, percent change, "
        "and average volume for each row."
    )
    default_confidence = 0.4

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "price": {"type": "number"},
                            "volume": {"type": "number"},
                            "pct_change": {"type": "number"},
                            "avg_volume": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 7. Finviz Oversold / 52-Week Low
# ---------------------------------------------------------------------------


class FinvizOversold(SchemaWebSource):
    name = "finviz_oversold"
    signal_type = "oversold"
    frequency = "daily"
    # v=171 for technical view (includes RSI column)
    url = "https://finviz.com/screener.ashx?v=171&f=ta_rsi_os,cap_midover"
    extraction_prompt = (
        "Extract all stock tickers from the technical screener table showing "
        "oversold stocks (RSI below 30). Include ticker, company name, price, "
        "RSI value, and 52-week high/low if available."
    )
    proxy = "stealth"
    default_confidence = 0.45

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "price": {"type": "number"},
                            "rsi": {"type": "number"},
                            "high_52w": {"type": "number"},
                            "low_52w": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 8. Finviz High Dividend Yield (replaces Dividend.com)
# ---------------------------------------------------------------------------


class FinvizDividend(SchemaWebSource):
    name = "finviz_dividend"
    signal_type = "high_dividend"
    frequency = "weekly"
    url = "https://finviz.com/screener.ashx?v=111&f=fa_div_o5,cap_midover"
    extraction_prompt = (
        "Extract all stock tickers from the screener showing high dividend yield "
        "stocks (>5%). Include ticker, company name, sector, dividend yield, "
        "P/E ratio, market cap, and price."
    )
    proxy = "stealth"
    default_confidence = 0.45


# ---------------------------------------------------------------------------
# 9. SEC 13F Institutional Filings
# ---------------------------------------------------------------------------


class SEC13FFilings(SchemaWebSource):
    name = "sec_13f"
    signal_type = "institutional_filing"
    frequency = "weekly"
    url = "https://efts.sec.gov/LATEST/search-index?forms=13F-HR&dateRange=custom&startdt={start_date}&enddt={end_date}"
    headers = _SEC_HEADERS
    extraction_prompt = (
        "Extract all stock tickers mentioned in these 13F institutional "
        "holdings filings. Include the filer name, ticker, number of shares, "
        "and reported value if available."
    )
    default_confidence = 0.55

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "filer_name": {"type": "string"},
                            "shares": {"type": "number"},
                            "reported_value": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 10. Yahoo Finance Earnings Calendar (replaces Earnings Whispers)
# ---------------------------------------------------------------------------


class YahooEarningsCalendar(SchemaWebSource):
    name = "yahoo_earnings"
    signal_type = "upcoming_earnings"
    frequency = "weekly"
    url = "https://finance.yahoo.com/calendar/earnings/"
    extraction_prompt = (
        "Extract all stock tickers from the earnings calendar. Include ticker, "
        "company name, earnings date, EPS estimate, and EPS actual if reported."
    )
    default_confidence = 0.35

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "earnings_date": {"type": "string"},
                            "eps_estimate": {"type": "number"},
                            "eps_actual": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# 11. Finviz Undervalued Growth (replaces Simply Wall St)
# ---------------------------------------------------------------------------


class FinvizUndervaluedGrowth(SchemaWebSource):
    name = "finviz_growth"
    signal_type = "undervalued_growth"
    frequency = "weekly"
    url = "https://finviz.com/screener.ashx?v=111&f=fa_pe_u20,fa_peg_u1,fa_salesqoq_o10,cap_midover"
    extraction_prompt = (
        "Extract all stock tickers from the screener showing undervalued growth "
        "stocks (P/E under 20, PEG under 1, sales growth >10%). Include ticker, "
        "company name, sector, P/E, PEG ratio, market cap, and price."
    )
    proxy = "stealth"
    default_confidence = 0.5


# ---------------------------------------------------------------------------
# 12. StockAnalysis Insider Trading
# ---------------------------------------------------------------------------


class StockAnalysisInsider(SchemaWebSource):
    name = "stockanalysis_insider"
    signal_type = "insider_buy"
    frequency = "daily"
    url = "https://stockanalysis.com/actions/insider-buys/"
    wait_for = 3000
    extraction_prompt = (
        "Extract all stock tickers from the insider buying activity table. "
        "Include ticker, company name, insider name, transaction type, "
        "number of shares, and transaction value."
    )
    default_confidence = 0.6

    def extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "insider_name": {"type": "string"},
                            "shares": {"type": "number"},
                            "transaction_value": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }


# ---------------------------------------------------------------------------
# Default registry with all sources registered
# ---------------------------------------------------------------------------


def build_default_registry() -> SourceRegistry:
    """Create a ``SourceRegistry`` pre-loaded with all 12 sources."""
    registry = SourceRegistry()
    registry.register(FinvizValueScreener())
    registry.register(FinvizInsiderBuying())
    registry.register(SECEdgarFullText())
    registry.register(DataromaGurus())
    registry.register(FinvizAnalystUpgrades())
    registry.register(BarchartVolumeLeaders())
    registry.register(FinvizOversold())
    registry.register(FinvizDividend())
    registry.register(SEC13FFilings())
    registry.register(YahooEarningsCalendar())
    registry.register(FinvizUndervaluedGrowth())
    registry.register(StockAnalysisInsider())
    return registry
