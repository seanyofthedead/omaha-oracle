"""
Manual test for Graham-Dodd quantitative screening logic.

Uses hardcoded financial data for 5 companies. No AWS calls — thresholds
come from defaults; company and financial data are constructed in-memory.

Run from project root:
  python tests\\manual\\test_quant_screen.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from analysis.quant_screen.financials import (
    DEFAULT_THRESHOLDS,
    _load_thresholds,
)
from analysis.quant_screen.screener import screen_company

# ------------------------------------------------------------------ #
# Mock config client — returns None so _load_thresholds uses defaults  #
# ------------------------------------------------------------------ #
_mock_config = MagicMock()
_mock_config.get_item.return_value = None


def _failed_criteria(result: dict, thresholds: dict) -> list[str]:
    """Determine which screening criteria failed from result and thresholds."""
    failed: list[str] = []
    pe = result.get("pe", 0)
    pb = result.get("pb", 0)
    pe_max = float(thresholds.get("pe_max", 15))
    pb_max = float(thresholds.get("pb_max", 1.5))
    de_max = float(thresholds.get("debt_equity_max", 0.5))
    roic_min = float(thresholds.get("roic_10y_min_pct", 12)) / 100
    fcf_min = int(thresholds.get("positive_fcf_min_years", 8))
    piot_min = int(thresholds.get("piotroski_min", 6))

    if pe > 0 and pe >= pe_max:
        failed.append("P/E")
    if pb > 0 and pb >= pb_max:
        failed.append("P/B")
    if result.get("debt_equity", 0) >= de_max:
        failed.append("Debt/Equity")
    if result.get("roic_10y_avg", 0) < roic_min:
        failed.append("ROIC")
    if result.get("positive_fcf_years", 0) < fcf_min:
        failed.append("FCF")
    if result.get("piotroski_score", 0) < piot_min:
        failed.append("Piotroski")
    if pe <= 0 and result.get("owner_earnings", 0) < 0:
        failed.append("Negative Earnings")
    return failed


def _build_financial_items(
    years: list[int],
    revenue: list[float],
    net_income: list[float],
    total_assets: float,
    total_liabilities: float,
    equity: float,
    current_assets: float,
    current_liabilities: float,
    ocf: float,
    capex: float,
    depreciation: float,
    ltd: float,
    shares: float,
    overrides: dict[int, dict[str, float]] | None = None,
) -> list[dict]:
    """Build financial items for _aggregate_financials_by_year.
    overrides: optional per-year metric overrides, e.g. {2024: {"operating_cash_flow": 100}}
    """
    overrides = overrides or {}
    items: list[dict] = []
    for i, year in enumerate(years):
        o = overrides.get(year, {})
        base = f"{year}-09-30"
        rev = o.get("revenue", revenue[i] if i < len(revenue) else revenue[-1])
        ni = o.get("net_income", net_income[i] if i < len(net_income) else net_income[-1])
        ta = o.get("total_assets", total_assets)
        tl = o.get("total_liabilities", total_liabilities)
        eq = o.get("stockholders_equity", equity)
        ca = o.get("current_assets", current_assets)
        cl = o.get("current_liabilities", current_liabilities)
        oc = o.get("operating_cash_flow", ocf)
        cap = o.get("capex", capex)
        dep = o.get("depreciation", depreciation)
        lt = o.get("long_term_debt", ltd)
        sh = o.get("shares_outstanding", shares)
        items.append({"period_end_date": base, "metric_name": "revenue", "value": rev})
        items.append({"period_end_date": base, "metric_name": "net_income", "value": ni})
        items.append({"period_end_date": base, "metric_name": "total_assets", "value": ta})
        items.append({"period_end_date": base, "metric_name": "total_liabilities", "value": tl})
        items.append({"period_end_date": base, "metric_name": "stockholders_equity", "value": eq})
        items.append({"period_end_date": base, "metric_name": "current_assets", "value": ca})
        items.append({"period_end_date": base, "metric_name": "current_liabilities", "value": cl})
        items.append({"period_end_date": base, "metric_name": "operating_cash_flow", "value": oc})
        items.append({"period_end_date": base, "metric_name": "capex", "value": cap})
        items.append({"period_end_date": base, "metric_name": "depreciation", "value": dep})
        items.append({"period_end_date": base, "metric_name": "long_term_debt", "value": lt})
        items.append({"period_end_date": base, "metric_name": "shares_outstanding", "value": sh})
    return items


# ------------------------------------------------------------------ #
# Company definitions (10 years: 2015–2024, most recent first)        #
# All monetary values in billions except per-share and shares         #
# ------------------------------------------------------------------ #

YEARS = [2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015]

COMPANIES = [
    {
        "name": "Apple",
        "ticker": "AAPL",
        "company": {
            "ticker": "AAPL",
            "trailingPE": 33.3,
            "priceToBook": 62.5,
            "trailingEps": 7.50,
            "bookValue": 4.00,
            "marketCap": 250 * 15.4,
        },
        "financials": lambda: _build_financial_items(
            YEARS,
            revenue=[394, 383, 365, 274, 260, 229, 215, 182, 170, 156],
            net_income=[97, 100, 94, 73, 57, 55, 48, 45, 37, 34],
            total_assets=352,
            total_liabilities=290,
            equity=62,
            current_assets=143,
            current_liabilities=145,
            ocf=118,
            capex=11,
            depreciation=11,
            ltd=98,
            shares=15.4,
        ),
    },
    {
        "name": "Coca-Cola",
        "ticker": "KO",
        "company": {
            "ticker": "KO",
            "trailingPE": 28.8,
            "priceToBook": 11.1,
            "trailingEps": 2.50,
            "bookValue": 6.50,
            "marketCap": 72 * 4.3,
        },
        "financials": lambda: _build_financial_items(
            YEARS,
            revenue=[47, 46, 43, 33, 37, 32, 31, 35, 36, 41],
            net_income=[10.7, 9.5, 10.2, 7.7, 8.9, 6.4, 1.2, 6.5, 6.5, 7.1],
            total_assets=100,
            total_liabilities=72,
            equity=28,
            current_assets=22,
            current_liabilities=20,
            ocf=12,
            capex=1.9,
            depreciation=1.2,
            ltd=36,
            shares=4.3,
        ),
    },
    {
        "name": "Berkshire Hathaway",
        "ticker": "BRK-B",
        "company": {
            "ticker": "BRK-B",
            "trailingPE": 12.0,
            "priceToBook": 1.35,
            "trailingEps": 45.00,
            "bookValue": 400.00,
            "marketCap": 540 * 1.3,
        },
        "financials": lambda: _build_financial_items(
            YEARS,
            revenue=[364, 302, 276, 245, 255, 247, 225, 210, 197, 250],
            net_income=[92, 90, 88, 86, 84, 82, 80, 78, 76, 74],
            total_assets=1070,
            total_liabilities=500,
            equity=570,
            current_assets=190,
            current_liabilities=120,
            ocf=50,
            capex=16,
            depreciation=12,
            ltd=120,
            shares=1.3,
            overrides={
                2024: {
                    "operating_cash_flow": 100,
                    "capex": 10,
                    "depreciation": 10,
                    "long_term_debt": 100,
                    "current_assets": 220,
                    "current_liabilities": 100,
                },
                2023: {
                    "operating_cash_flow": 50,
                    "capex": 16,
                    "depreciation": 12,
                    "long_term_debt": 120,
                    "current_assets": 190,
                    "current_liabilities": 120,
                },
                2016: {
                    "operating_cash_flow": 50,
                    "capex": 16,
                    "depreciation": 12,
                    "long_term_debt": 105,
                    "current_assets": 200,
                    "current_liabilities": 110,
                },
                2015: {
                    "net_income": 80,
                    "operating_cash_flow": 90,
                    "capex": 10,
                    "depreciation": 10,
                    "long_term_debt": 95,
                    "current_assets": 240,
                    "current_liabilities": 95,
                },
            },
        ),
    },
    {
        "name": "GE",
        "ticker": "GE",
        "company": {
            "ticker": "GE",
            "trailingPE": 50.0,
            "priceToBook": 3.0,
            "trailingEps": 0.30,
            "bookValue": 5.00,
            "marketCap": 15 * 8.7,
        },
        "financials": lambda: _build_financial_items(
            YEARS,
            revenue=[75, 80, 95, 121, 123, 117, 119, 115, 148, 146],
            net_income=[5, -5, -22, -6, 8, -10, -7, 1, 8, 6],
            total_assets=265,
            total_liabilities=230,
            equity=35,
            current_assets=80,
            current_liabilities=60,
            ocf=6,
            capex=5,
            depreciation=4,
            ltd=70,
            shares=8.7,
            overrides={
                2022: {"operating_cash_flow": 2, "capex": 5},
                2021: {"operating_cash_flow": 1, "capex": 5},
                2020: {"operating_cash_flow": -2, "capex": 5},
                2019: {"operating_cash_flow": 3, "capex": 6},
                2018: {"operating_cash_flow": 0, "capex": 5},
            },
        ),
    },
    {
        "name": "Snowflake",
        "ticker": "SNOW",
        "company": {
            "ticker": "SNOW",
            "trailingPE": -120,
            "priceToBook": 15.0,
            "trailingEps": -1.50,
            "bookValue": 12.00,
            "marketCap": 180 * 0.33,
        },
        "financials": lambda: _build_financial_items(
            YEARS,
            revenue=[3.4, 2.8, 2.1, 1.2, 0.6, 0.26, 0.10, 0.03, 0.01, 0.005],
            net_income=[-0.80, -0.83, -0.80, -0.68, -0.54, -0.35, -0.18, -0.12, -0.06, -0.03],
            total_assets=9.5,
            total_liabilities=3.2,
            equity=6.3,
            current_assets=5.0,
            current_liabilities=2.0,
            ocf=-0.1,
            capex=0.3,
            depreciation=0.2,
            ltd=0,
            shares=0.33,
        ),
    },
]


def _print_scorecard(name: str, ticker: str, result: dict, failed: list[str], passed: bool) -> None:
    """Print detailed scorecard for one company."""
    th = DEFAULT_THRESHOLDS
    print(f"\n{'=' * 60}")
    print(f"{name} ({ticker})")
    print("=" * 60)
    print("Computed metrics:")
    print(f"  Owner earnings:    {result.get('owner_earnings', 0):.2f}")
    print(f"  EPV:               {result.get('epv', 0):.2f}")
    print(f"  Graham number:     {result.get('graham_number', 0):.2f}")
    print(f"  NNWC:              {result.get('net_net_working_capital', 0):.2f}")
    print(f"  ROIC (current):    {result.get('roic_current', 0) * 100:.1f}%")
    print(f"  ROIC (10y avg):    {result.get('roic_10y_avg', 0) * 100:.1f}%")
    print(f"  Debt/Equity:       {result.get('debt_equity', 0):.2f}")
    print(f"  FCF yield:         {result.get('fcf_yield', 0) * 100:.1f}%")
    print(f"  Piotroski score:   {result.get('piotroski_score', 0)}/9")
    print(f"  Positive FCF yrs:  {result.get('positive_fcf_years', 0)}")
    print(f"  P/E:               {result.get('pe', 0):.1f}")
    print(f"  P/B:               {result.get('pb', 0):.1f}")
    print()
    print("Screening criteria:")
    pe, pb = result.get("pe", 0), result.get("pb", 0)
    pe_ok = pe <= 0 or pe < th["pe_max"]
    pb_ok = pb <= 0 or pb < th["pb_max"]
    de_ok = result.get("debt_equity", 0) < th["debt_equity_max"]
    roic_ok = result.get("roic_10y_avg", 0) >= th["roic_10y_min_pct"] / 100
    fcf_ok = result.get("positive_fcf_years", 0) >= th["positive_fcf_min_years"]
    piot_ok = result.get("piotroski_score", 0) >= th["piotroski_min"]
    print(f"  P/E < {th['pe_max']}:        {pe:.1f}  {'PASS' if pe_ok else 'FAIL'}")
    print(f"  P/B < {th['pb_max']}:        {pb:.1f}  {'PASS' if pb_ok else 'FAIL'}")
    de_val = result.get("debt_equity", 0)
    de_status = "PASS" if de_ok else "FAIL"
    print(
        f"  D/E < {th['debt_equity_max']}:        "
        f"{de_val:.2f}  {de_status}"
    )
    roic_val = result.get("roic_10y_avg", 0) * 100
    roic_status = "PASS" if roic_ok else "FAIL"
    print(
        f"  ROIC 10y >= {th['roic_10y_min_pct']}%:   "
        f"{roic_val:.1f}%  {roic_status}"
    )
    fcf_val = result.get("positive_fcf_years", 0)
    fcf_status = "PASS" if fcf_ok else "FAIL"
    print(
        f"  FCF+ yrs >= {th['positive_fcf_min_years']}:     "
        f"{fcf_val}  {fcf_status}"
    )
    piot_val = result.get("piotroski_score", 0)
    piot_status = "PASS" if piot_ok else "FAIL"
    print(
        f"  Piotroski >= {th['piotroski_min']}:    "
        f"{piot_val}  {piot_status}"
    )
    if result.get("owner_earnings", 0) < 0 and "Negative Earnings" in failed:
        print("  Positive earnings:   N/A  FAIL (negative)")
    print()
    if passed:
        print("Overall: PASSED SCREEN")
    else:
        print(f"Overall: FAILED SCREEN  [{', '.join(failed)}]")


def main() -> None:
    thresholds = _load_thresholds(_mock_config)
    summary: list[tuple[str, str, bool, list[str]]] = []

    for c in COMPANIES:
        items = c["financials"]()
        fin_client = MagicMock()
        fin_client.query.return_value = items
        result, passed = screen_company(c["ticker"], c["company"], fin_client, thresholds)
        failed = _failed_criteria(result, thresholds)
        summary.append((c["ticker"], c["name"], passed, failed))
        _print_scorecard(c["name"], c["ticker"], result, failed, passed)

    print("\n")
    print("=" * 43)
    print("SCREENING SUMMARY")
    print("=" * 43)
    for ticker, name, passed, failed in summary:
        status = "PASS" if passed else "FAIL"
        fail_str = f"  [{', '.join(failed)}]" if failed else ""
        print(f"{ticker:6} ({name:12}) {status:4}{fail_str}")
    print("=" * 43)


if __name__ == "__main__":
    main()
