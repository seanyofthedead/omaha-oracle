# Visual QA Report — Omaha Oracle Dashboard

**Date:** 2026-03-21
**Reviewer:** Claude (automated pixel-perfect QA pass)
**Scope:** All 6 dashboard pages, shared styles, formatting, charts, sidebar

---

## Defect Table

| # | Page | Category | Defect | Fix Applied |
|---|------|----------|--------|-------------|
| 1 | All (fmt) | Edge Case | `fmt_currency(-1234)` rendered `$-1,234` instead of `-$1,234` — sign placed after dollar sign | Fixed: negative values now render as `-$1,234` with sign before `$` |
| 2 | All (fmt) | Edge Case | `fmt_datetime` used `%-I` (Unix-only strftime code) — crashes on Windows with `ValueError` | Fixed: replaced with `%I` (zero-padded, cross-platform) |
| 3 | All (charts) | Consistency | Chart font family `"Inter, Segoe UI"` did not match Streamlit theme font `"DM Sans"` from config.toml | Fixed: prepended `"DM Sans"` to chart font stack |
| 4 | Watchlist | Empty State | Quality Scores tab showed raw `0` for unscored moat/mgmt instead of em-dash placeholder | Fixed: unscored values now display `—`; scored values show `7.0` format |
| 5 | Feedback Loop | Edge Case | Lesson text column always appended `"..."` even for text shorter than 60 characters | Fixed: ellipsis only added when text exceeds 60 chars |
| 6 | Feedback Loop | Edge Case | Lesson text column would show `"..."` for None/empty text instead of em-dash | Fixed: empty/None text now falls through to `fmt_null(None)` → `—` |
| 7 | Letters | Alignment | Hero metrics used only 2 columns — cards were ~2x wider than every other page's metric cards | Fixed: changed to 4-column grid with 2 spacer columns for consistent card width |

---

## Categories Checked (All Pages)

### 1. CONSISTENCY
- **Title pattern:** All pages use `st.title()` + `st.caption()` — consistent
- **Hero metrics:** All pages use `st.columns(N, gap="large", vertical_alignment="bottom")` — consistent after fix #7
- **Metric formatting:** All currency through `fmt_currency`/`fmt_currency_short`, all percentages through `fmt_pct`, all dates through `fmt_date`/`fmt_datetime`, all nulls through `fmt_null` — consistent
- **Tab pattern:** All pages with tabbed content use `st.tabs()` + `st.container(border=True)` — consistent
- **Expander pattern:** Supplementary content in `st.expander()` at bottom of each page — consistent
- **Divider placement:** Divider between hero metrics and primary content on every page — consistent

### 2. ALIGNMENT
- All hero metric rows use `vertical_alignment="bottom"` — aligned
- Chart containers use `st.container(border=True)` uniformly — aligned
- DataFrames all use `use_container_width=True, hide_index=True` — aligned

### 3. COLOR COMPLIANCE
- Accent blue `#6C9EFF` used for: primary color (config.toml), chart bars, active tab border — consistent
- Accent green `#4CAF50` used for: BUY signals, dev environment badge — consistent
- Accent red `#F44336` used for: SELL signals, prod environment badge, mistake rate chart — consistent
- Muted gray `rgba(255,255,255,0.25)` used for: chart reference lines (budget, 35% limit, target) — consistent
- No default Plotly blue or random grays found in any chart
- Dark background `#0E1117` / `#161B22` consistently applied via config.toml

### 4. EMPTY STATES
- **Portfolio:** Shows `st.info()` when no positions — graceful
- **Watchlist:** Shows `st.info()` when no candidates; `st.warning()` for missing IVs — graceful
- **Signals:** Shows `st.info()` when no decisions; per-tab empty states for BUY/SELL — graceful
- **Cost Tracker:** Shows `st.info()` in both Spend Trend and Monthly Breakdown tabs — graceful
- **Letters:** Shows `st.info()` when no letters on file — graceful
- **Feedback Loop:** Shows `st.info()` per tab (lessons, trends, calibration) — graceful

### 5. EDGE CASES
- Very long text in lesson table: truncated at 60 chars with `...` (fix #5)
- Very large numbers: `fmt_currency_short` handles B/M/K suffixes correctly
- Negative values: `fmt_currency` now properly signs (fix #1)
- None/NaN: all formatting functions return em-dash `—`
- Windows platform: `fmt_datetime` is now cross-platform (fix #2)

### 6. SIDEBAR
- Logo rendering via `st.logo()` with main + icon variants — consistent across all pages
- Subtitle "Portfolio Intelligence Dashboard" — present on every page
- Navigation radio with material design icons — consistent
- Footer: environment badge (green/red), version, refresh timestamp — consistent
- Page-specific sidebar filters (Signals: max slider; Cost: months slider; Letters: selectbox) appear below navigation

### 7. ERROR RESILIENCE
- All data-loading functions wrapped in `try/except DataLoadError`
- `DataLoadError` carries user-friendly messages (no tracebacks)
- Specific guidance for: missing credentials, network issues, missing tables, access denied
- `st.cache_data` does not cache exceptions — failures retry on next rerun
- Password auth blocks access before any data loading

---

## Summary

**7 found, 7 fixed, 0 deferred**

All defects were fixable without design decisions. No new features introduced.

### Files Modified
- `src/dashboard/fmt.py` — fixes #1, #2
- `src/dashboard/charts.py` — fix #3
- `src/dashboard/views/watchlist.py` — fix #4
- `src/dashboard/views/feedback_loop.py` — fixes #5, #6
- `src/dashboard/views/letters.py` — fix #7

### Pre-existing (Not From This Pass)
- `src/dashboard/styles.py:43` — CSS selector exceeds 100-char line limit (ruff E501). This is inside a raw CSS string and cannot be meaningfully shortened without splitting the selector.
