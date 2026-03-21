"""
Piotroski F-Score calculator (0–9).
"""

from __future__ import annotations


def piotroski_score(by_year: dict[int, dict[str, float]], years_sorted: list[int]) -> int:
    """
    Piotroski F-Score (0–9). Uses available data; scores 0 for missing components.
    """
    if len(years_sorted) < 2:
        return 0
    score = 0

    def get(y: int, m: str) -> float:
        return by_year.get(y, {}).get(m, 0.0)

    # Current year = most recent
    curr_y = years_sorted[-1]
    prev_y = years_sorted[-2]

    # 1. ROA > 0 (Net Income / Total Assets)
    ni = get(curr_y, "net_income")
    ta = get(curr_y, "total_assets")
    roa_curr = ni / ta if ta else 0
    if roa_curr > 0:
        score += 1

    # 2. Operating Cash Flow > 0
    ocf = get(curr_y, "operating_cash_flow")
    if ocf > 0:
        score += 1

    # 3. Change in ROA > 0
    ta_prev = get(prev_y, "total_assets")
    roa_prev = get(prev_y, "net_income") / ta_prev if ta_prev else 0
    if roa_curr > roa_prev:
        score += 1

    # 4. Cash flow from ops > Net Income
    if ocf > ni:
        score += 1

    # 5. Change in leverage < 0 (decrease in LTD/Assets)
    ltd_curr = get(curr_y, "long_term_debt")
    ltd_prev = get(prev_y, "long_term_debt")
    lev_curr = ltd_curr / ta if ta else 0
    lev_prev = ltd_prev / ta_prev if ta_prev else 0
    if lev_curr < lev_prev:
        score += 1

    # 6. Change in current ratio > 0
    ca_curr = get(curr_y, "current_assets")
    cl_curr = get(curr_y, "current_liabilities")
    ca_prev = get(prev_y, "current_assets")
    cl_prev = get(prev_y, "current_liabilities")
    cr_curr = ca_curr / cl_curr if cl_curr else 0
    cr_prev = ca_prev / cl_prev if cl_prev else 0
    if cr_curr > cr_prev:
        score += 1

    # 7. Change in shares outstanding <= 0 (no dilution)
    sh_curr = get(curr_y, "shares_outstanding")
    sh_prev = get(prev_y, "shares_outstanding")
    if sh_curr <= sh_prev and sh_prev > 0:
        score += 1

    # 8. Change in gross margin > 0 (we don't have gross margin; use operating margin proxy)
    rev_curr = get(curr_y, "revenue")
    rev_prev = get(prev_y, "revenue")
    dep_curr = get(curr_y, "depreciation") + get(curr_y, "capex")
    dep_prev = get(prev_y, "depreciation") + get(prev_y, "capex")
    om_curr = (rev_curr - dep_curr) / rev_curr if rev_curr else 0
    om_prev = (rev_prev - dep_prev) / rev_prev if rev_prev else 0
    if om_curr > om_prev:
        score += 1

    # 9. Change in asset turnover > 0
    at_curr = rev_curr / ta if ta else 0
    at_prev = rev_prev / ta_prev if ta_prev else 0
    if at_curr > at_prev:
        score += 1

    return score
