"""
Reusable mock company data, decisions, and lessons for all tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# ------------------------------------------------------------------ #
# Apple — should pass most quant screens (quality company)            #
# ------------------------------------------------------------------ #

APPLE_COMPANY = {
    "ticker": "AAPL",
    "trailingPE": 28.5,
    "priceToBook": 45.0,
    "trailingEps": 6.0,
    "bookValue": 4.5,
    "marketCap": 3_000_000_000_000,
    "sector": "Technology",
    "industry": "Consumer Electronics",
}

# 10 years of strong financials. Piotroski uses curr=years_sorted[-1], prev=years_sorted[-2]
# (i.e. 2015 vs 2016). We need 2015 to outperform 2016 for improvements.
APPLE_FINANCIALS_BY_YEAR: dict[int, dict[str, float]] = {}
for y in range(2015, 2025):
    # 2015 slightly better than 2016 for Piotroski (ROA, leverage, CR, OM, AT)
    if y == 2015:
        ni, ocf, ltd, ca, rev = 60e9, 85e9, 25e9, 180e9, 235e9
    elif y == 2016:
        ni, ocf, ltd, ca, rev = 50e9, 70e9, 35e9, 150e9, 220e9
    else:
        ni = 50_000_000_000 + (y - 2015) * 5_000_000_000
        ocf = 70_000_000_000 + (y - 2015) * 5_000_000_000
        ltd = 30_000_000_000
        ca = 150_000_000_000
        rev = 200_000_000_000 + (y - 2015) * 20_000_000_000
    APPLE_FINANCIALS_BY_YEAR[y] = {
        "revenue": rev,
        "net_income": ni,
        "total_assets": 300_000_000_000,
        "total_liabilities": 150_000_000_000,
        "stockholders_equity": 100_000_000_000 + (y - 2015) * 10_000_000_000,
        "long_term_debt": ltd,
        "operating_cash_flow": ocf,
        "capex": 10_000_000_000,
        "depreciation": 12_000_000_000,
        "current_assets": ca,
        "current_liabilities": 80_000_000_000,
        "shares_outstanding": 16_000_000_000,
    }


# Convert to handler's financial items format (period_end_date, metric_name, value)
def _financials_to_items(ticker: str, by_year: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for year, metrics in by_year.items():
        period = f"{year}-09-30"
        for metric_name, value in metrics.items():
            items.append(
                {
                    "ticker": ticker,
                    "period_end_date": period,
                    "metric_name": metric_name,
                    "value": value,
                }
            )
    return items


APPLE_FINANCIAL_ITEMS = _financials_to_items("AAPL", APPLE_FINANCIALS_BY_YEAR)

# Override Apple to have conservative multiples for quant screen (PE < 15, PB < 1.5)
APPLE_COMPANY_QUANT_PASS = {
    **APPLE_COMPANY,
    "trailingPE": 12.0,
    "priceToBook": 1.2,
    "trailingEps": 12.0,
    "bookValue": 120.0,
    "marketCap": 1_800_000_000_000,
}

# ------------------------------------------------------------------ #
# GameStop — should fail (negative earnings, volatile)                 #
# ------------------------------------------------------------------ #

GAMESTOP_COMPANY = {
    "ticker": "GME",
    "trailingPE": -5.0,  # Negative earnings
    "priceToBook": 2.5,
    "trailingEps": -2.0,
    "bookValue": 5.0,
    "marketCap": 5_000_000_000,
    "sector": "Consumer Cyclical",
    "industry": "Specialty Retail",
}

# Volatile: negative NI some years, erratic revenue
GAMESTOP_FINANCIALS_BY_YEAR: dict[int, dict[str, float]] = {}
for i, y in enumerate(range(2015, 2025)):
    ni = -100_000_000 if i % 2 == 0 else 50_000_000
    rev = 5_000_000_000 + (i % 3) * 2_000_000_000
    GAMESTOP_FINANCIALS_BY_YEAR[y] = {
        "revenue": rev,
        "net_income": ni,
        "total_assets": 2_000_000_000,
        "total_liabilities": 1_500_000_000,
        "stockholders_equity": 500_000_000,
        "long_term_debt": 400_000_000,
        "operating_cash_flow": 100_000_000 if i % 2 == 1 else -50_000_000,
        "capex": 50_000_000,
        "depreciation": 80_000_000,
        "current_assets": 1_200_000_000,
        "current_liabilities": 800_000_000,
        "shares_outstanding": 300_000_000,
    }

GAMESTOP_FINANCIAL_ITEMS = _financials_to_items("GME", GAMESTOP_FINANCIALS_BY_YEAR)

# ------------------------------------------------------------------ #
# Edge cases: zero equity, negative revenue, missing data              #
# ------------------------------------------------------------------ #

ZERO_EQUITY_COMPANY = {
    "ticker": "ZERO",
    "trailingPE": 10.0,
    "priceToBook": 0.0,
    "trailingEps": 1.0,
    "bookValue": 0.0,
    "marketCap": 1_000_000_000,
}

ZERO_EQUITY_FINANCIALS: dict[int, dict[str, float]] = {
    2024: {
        "revenue": 100_000_000,
        "net_income": 10_000_000,
        "total_assets": 50_000_000,
        "stockholders_equity": 0.0,
        "long_term_debt": 20_000_000,
        "operating_cash_flow": 15_000_000,
        "capex": 5_000_000,
        "depreciation": 2_000_000,
        "current_assets": 30_000_000,
        "current_liabilities": 25_000_000,
        "shares_outstanding": 10_000_000,
    },
    2023: {
        "revenue": 90_000_000,
        "net_income": 8_000_000,
        "total_assets": 45_000_000,
        "stockholders_equity": 0.0,
        "long_term_debt": 18_000_000,
        "operating_cash_flow": 12_000_000,
        "capex": 4_000_000,
        "depreciation": 2_000_000,
        "current_assets": 28_000_000,
        "current_liabilities": 24_000_000,
        "shares_outstanding": 10_000_000,
    },
}

NEGATIVE_REVENUE_COMPANY = {
    "ticker": "NEG",
    "trailingPE": 5.0,
    "priceToBook": 0.5,
    "marketCap": 500_000_000,
}

NEGATIVE_REVENUE_FINANCIALS: dict[int, dict[str, float]] = {
    2024: {
        "revenue": -10_000_000,
        "net_income": -50_000_000,
        "total_assets": 100_000_000,
        "stockholders_equity": 80_000_000,
        "long_term_debt": 10_000_000,
        "operating_cash_flow": -20_000_000,
        "capex": 5_000_000,
        "depreciation": 3_000_000,
        "current_assets": 60_000_000,
        "current_liabilities": 20_000_000,
        "shares_outstanding": 20_000_000,
    },
    2023: {
        "revenue": 5_000_000,
        "net_income": -30_000_000,
        "total_assets": 120_000_000,
        "stockholders_equity": 90_000_000,
        "long_term_debt": 15_000_000,
        "operating_cash_flow": -15_000_000,
        "capex": 4_000_000,
        "depreciation": 3_000_000,
        "current_assets": 65_000_000,
        "current_liabilities": 25_000_000,
        "shares_outstanding": 20_000_000,
    },
}

# ------------------------------------------------------------------ #
# Decisions (for postmortem / feedback loop tests)                    #
# ------------------------------------------------------------------ #


def make_decision(
    ticker: str,
    signal: str,
    price_at_decision: float,
    timestamp: str | None = None,
    sector: str = "Technology",
    moat_score: float | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    ts = timestamp or (now - timedelta(days=90)).isoformat()
    return {
        "decision_id": f"dec-{ticker}-{ts[:10]}",
        "ticker": ticker,
        "signal": signal,
        "timestamp": ts,
        "decision_type": "BUY" if signal == "BUY" else "ANALYSIS",
        "payload": {
            "ticker": ticker,
            "price_at_decision": price_at_decision,
            "limit_price": price_at_decision,
            "current_price": price_at_decision,
            "sector": sector,
            "moat_score": moat_score,
        },
    }


# BAD_BUY: bought at 100, now 75 (-25% > 20%)
BAD_BUY_DECISION = make_decision("BAD", "BUY", 100.0, moat_score=7)

# GOOD_SELL: sold at 100, now 85 (-15% > 10%)
GOOD_SELL_DECISION = make_decision("SOLD", "SELL", 100.0)

# MISSED_OPPORTUNITY: passed at 100, now 140 (+40% > 30%)
MISSED_OPP_DECISION = make_decision("MISS", "NO_BUY", 100.0)
MISSED_OPP_DECISION["payload"]["signal"] = "NO_BUY"

# CORRECT_PASS: passed at 100, now 95 (flat/down)
CORRECT_PASS_DECISION = make_decision("PASS", "NO_BUY", 100.0)
CORRECT_PASS_DECISION["payload"]["signal"] = "NO_BUY"

# ------------------------------------------------------------------ #
# Lessons (for LessonsClient tests)                                    #
# ------------------------------------------------------------------ #


def make_lesson(
    lesson_type: str,
    lesson_id: str,
    ticker: str = "",
    sector: str = "ALL",
    industry: str = "ALL",
    severity: str = "moderate",
    prompt_injection_text: str = "Test lesson text",
    quarter: str = "Q1_2025",
    expires_at: str | None = None,
    confidence_calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    expiry = expires_at or (now + timedelta(days=365)).isoformat()
    item: dict[str, Any] = {
        "lesson_type": lesson_type,
        "lesson_id": lesson_id,
        "ticker": ticker,
        "sector": sector,
        "industry": industry,
        "severity": severity,
        "description": f"Lesson {lesson_id}",
        "prompt_injection_text": prompt_injection_text,
        "quarter": quarter,
        "created_at": now.isoformat(),
        "expires_at": expiry,
        "active": True,
        "active_flag": "1",
    }
    if confidence_calibration:
        item["confidence_calibration"] = confidence_calibration
    return item


# Ticker-specific lesson (highest relevance)
LESSON_TICKER_AAPL = make_lesson(
    "moat_bias",
    "L1",
    ticker="AAPL",
    sector="Technology",
    prompt_injection_text="Apple has strong ecosystem moat.",
)

# Sector match
LESSON_SECTOR_TECH = make_lesson(
    "moat_bias",
    "L2",
    sector="Technology",
    industry="ALL",
    prompt_injection_text="Tech companies need recurring revenue.",
)

# Stage match (confidence_calibration.analysis_stage = moat_analysis)
LESSON_STAGE_MOAT = make_lesson(
    "moat_bias",
    "L3",
    sector="ALL",
    prompt_injection_text="Beware of moat bias in retail.",
    confidence_calibration={"analysis_stage": "moat_analysis", "sector": "ALL"},
)


# Expired lesson
def _expired_lesson() -> dict[str, Any]:
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    return make_lesson("moat_bias", "L-expired", expires_at=past)


LESSON_EXPIRED = _expired_lesson()

# Confidence calibration
LESSON_CONFIDENCE_08 = make_lesson(
    "confidence_calibration",
    "L-conf-1",
    confidence_calibration={
        "analysis_stage": "moat_analysis",
        "sector": "Technology",
        "adjustment_factor": 0.8,
    },
)
LESSON_CONFIDENCE_125 = make_lesson(
    "confidence_calibration",
    "L-conf-2",
    confidence_calibration={
        "analysis_stage": "moat_analysis",
        "sector": "Technology",
        "adjustment_factor": 1.25,
    },
)
