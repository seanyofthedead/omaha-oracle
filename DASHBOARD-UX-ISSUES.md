# Omaha Oracle — Dashboard UX Audit

> Generated 2026-03-21. Read-only audit — no code was modified.

## Findings Summary

- **CRITICAL:** 3 issues
- **HIGH:** 12 issues
- **MEDIUM:** 14 issues
- **LOW:** 8 issues
- **Total:** 37 issues

---

## CRITICAL

| # | Anti-Pattern | File | Line | Current Code | Recommended Fix |
|---|-------------|------|------|-------------|-----------------|
| 1 | **No loading states** — All 6 pages make AWS network calls (DynamoDB/S3) with zero user feedback. On slow connections or cold starts, the page appears frozen. | `src/dashboard/views/portfolio.py` | 14 | `data = load_portfolio()` | Wrap in `with st.spinner("Loading portfolio..."):` or use `st.status()` for multi-step loads |
| 2 | **No loading states** — Watchlist page fires ThreadPoolExecutor with up to 10 parallel DynamoDB queries with no spinner. | `src/dashboard/views/watchlist.py` | 14 | `candidates = load_watchlist_analysis()` | Wrap in `with st.spinner("Analyzing watchlist..."):` |
| 3 | **No loading states** — Feedback Loop page fires ThreadPoolExecutor to load up to 12 postmortems plus lessons, thresholds, and calibration data with no progress indication. | `src/dashboard/views/feedback_loop.py` | 21, 64, 72 | `lessons = load_lessons()` / `thresholds = load_config_thresholds()` / `keys = load_postmortem_keys()` | Wrap entire render body in `with st.spinner("Loading feedback data..."):` or use `st.status()` with step-by-step updates |

## HIGH

| # | Anti-Pattern | File | Line | Current Code | Recommended Fix |
|---|-------------|------|------|-------------|-----------------|
| 4 | **Default Streamlit theme** — `.streamlit/config.toml` has no `[theme]` section. Dashboard uses generic purple/white Streamlit branding instead of a custom financial-tool palette. | `.streamlit/config.toml` | 1-9 | Only `[server]` section present | Add `[theme]` section with `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `font` matching a professional finance aesthetic |
| 5 | **Raw dataframe — no column config** — Portfolio positions table dumps pre-formatted strings. No sorting, no conditional formatting (e.g., red/green for gain/loss), no column width control. | `src/dashboard/views/portfolio.py` | 51 | `st.dataframe(rows, use_container_width=True, hide_index=True)` | Use `st.dataframe(df, column_config={...})` with `st.column_config.NumberColumn` for currency/percentage, conditional color for Gain/Loss |
| 6 | **Raw dataframe — no column config** — Watchlist table shows raw numbers for Moat/Mgmt scores with no formatting, color scale, or progress bars. MoS% is a string, not sortable. | `src/dashboard/views/watchlist.py` | 38 | `st.dataframe(rows, use_container_width=True, hide_index=True)` | Use `column_config` with `st.column_config.ProgressColumn` for scores, `NumberColumn` for currency |
| 7 | **Raw dataframe — no column config** — Active Lessons table truncates text to 60 chars with `+ "..."` hardcoded in Python instead of using column width or expanders. | `src/dashboard/views/feedback_loop.py` | 33-36, 39 | `(lesson.get("prompt_injection_text") or ...)[:60] + "..."` then `st.dataframe(rows, ...)` | Use `column_config` with `st.column_config.TextColumn(width="large")` or move full text to an `st.expander` |
| 8 | **Raw dataframe — no column config** — Confidence Calibration table has no formatting for the Factor column (numeric precision, alignment). | `src/dashboard/views/feedback_loop.py` | 58 | `st.dataframe(rows, use_container_width=True, hide_index=True)` | Use `column_config` with `NumberColumn(format="%.2f")` |
| 9 | **No data caching** — Every page re-fetches all data from AWS on each interaction (slider change, page switch). No `@st.cache_data` anywhere in the codebase. Causes unnecessary latency and API costs. | `src/dashboard/data.py` | 23-214 | All `load_*()` functions are bare | Add `@st.cache_data(ttl=60)` to each `load_*()` function. Use `ttl` to auto-expire stale data. |
| 10 | **Signals page is a wall of text** — Unbounded list of signal cards with no pagination, filtering, or collapse. 100 signals render as a single scroll. | `src/dashboard/views/signals.py` | 21-42 | `for d in decisions:` loop with `st.subheader` + `st.write` + `st.divider` per signal | Add `st.tabs(["BUY", "SELL", "All"])` for filtering, or use `st.expander()` per signal to collapse details, add pagination |
| 11 | **No sidebar branding or context** — Sidebar has only title + "*Monitoring dashboard*" italic text. No environment indicator (dev/prod), no last-refresh timestamp, no version. | `src/dashboard/app.py` | 73-74 | `st.sidebar.title("Omaha Oracle")` / `st.sidebar.markdown("*Monitoring dashboard*")` | Add environment badge (`st.sidebar.badge`), last-refresh time, and a "Refresh" button that calls `st.cache_data.clear()` |
| 12 | **Portfolio page has no chart** — Only a flat data table for positions. No allocation pie chart, no sector breakdown, no performance-over-time visualization. | `src/dashboard/views/portfolio.py` | 32-51 | Builds `rows` list → `st.dataframe()` | Add a sector allocation pie chart (`st.plotly_chart` or `st.bar_chart`) and/or a portfolio value trend line |
| 13 | **Watchlist page has no visual hierarchy** — Title → table, nothing else. No summary metrics (total candidates, avg MoS, best opportunity), no filtering, no sorting controls. | `src/dashboard/views/watchlist.py` | 10-38 | `st.title` → build rows → `st.dataframe` | Add `st.metric` row at top (count, avg MoS%, top pick), add `st.selectbox` or `st.multiselect` for sector filter |
| 14 | **No height limits on dataframes** — All 4 dataframes render at full height regardless of row count. A table with 2 rows wastes space; a table with 200 rows overwhelms the page. | `portfolio.py:51`, `watchlist.py:38`, `feedback_loop.py:39,58` | — | `st.dataframe(rows, use_container_width=True, hide_index=True)` | Add `height=` parameter: e.g. `height=min(len(rows)*35 + 38, 400)` to cap height while respecting small tables |
| 15 | **Cost Tracker bar chart has no labels** — `st.bar_chart()` renders without axis labels, title, or budget reference line. Hard to read at a glance. | `src/dashboard/views/cost_tracker.py` | 36 | `st.bar_chart(df.set_index("month")["spent_usd"])` | Switch to `st.plotly_chart` or `st.altair_chart` with axis labels, a horizontal budget line, and month formatting |

## MEDIUM

| # | Anti-Pattern | File | Line | Current Code | Recommended Fix |
|---|-------------|------|------|-------------|-----------------|
| 16 | **No `st.container()` usage** — No logical grouping of related widgets. Metrics, tables, and charts are siblings at the same DOM level with no visual containment. | All view files | — | Flat sequence of `st.*` calls | Wrap related sections in `st.container(border=True)` for visual grouping (e.g., KPI row in one container, table in another) |
| 17 | **No `st.expander()` usage** — Dense information like signal reasoning, lesson text, and screening thresholds is always visible. No progressive disclosure. | `signals.py:30-40`, `feedback_loop.py:66` | — | Inline `st.write` / `st.json` | Wrap reasoning in `st.expander("Details")`, wrap `st.json(thresholds)` in `st.expander("View raw thresholds")` |
| 18 | **No `st.tabs()` usage** — Feedback Loop page has 4 sections stacked vertically (Lessons, Calibration, Thresholds, Mistake Rate). Could use tabs to reduce scrolling. | `src/dashboard/views/feedback_loop.py` | 20, 44, 63, 71 | Four sequential `st.subheader()` sections | Use `st.tabs(["Active Lessons", "Calibration", "Thresholds", "Mistake Trend"])` |
| 19 | **No `st.tabs()` usage** — Portfolio page could separate summary KPIs from the positions table using tabs or containers. | `src/dashboard/views/portfolio.py` | 12-51 | Flat layout: metrics → table | Use `st.container(border=True)` for KPI row, separate container for positions |
| 20 | **No metric deltas** — `st.metric` supports a `delta` parameter for showing change vs. prior period. All 7 metrics show static values with no trend context. | `portfolio.py:21-26`, `cost_tracker.py:19-26` | — | `st.metric("Budget", f"${...}")` | Add `delta` and `delta_color` parameters where period-over-period data is available |
| 21 | **No tooltips or help text** — None of the 7 metrics, 2 sliders, or 1 selectbox use the `help=` parameter. Financial terms like "MoS %" and "Half-Kelly" are unexplained. | All view files | — | No `help=` on any widget | Add `help="Margin of Safety: (IV - Price) / IV"` to MoS column, similar for other domain terms |
| 22 | **Inconsistent number formatting** — Portfolio formats currency as `$X,XXX` (no decimals) while Cost Tracker uses `$X.XX` (2 decimals). | `portfolio.py:43-44` vs `cost_tracker.py:21-23` | — | `f"${cost:,.0f}"` vs `f"${status.get('spent_usd', 0):,.2f}"` | Standardize: use 2 decimal places for dollar amounts under $1000, 0 for larger amounts, or consistently use 2 |
| 23 | **Moat/Mgmt scores are raw numbers** — Watchlist shows numeric scores (e.g., 7, 8) with no visual indicator of what's good vs. bad. No color, no bar, no scale reference. | `src/dashboard/views/watchlist.py` | 29-30 | `"Moat": moat` / `"Mgmt": mgmt` | Use `st.column_config.ProgressColumn(min_value=0, max_value=10)` or color-code ranges |
| 24 | **No favicon or page icon per page** — Only `app.py` sets a page icon. Individual pages have no visual identity in the sidebar. | `src/dashboard/app.py` | 76 | `st.sidebar.radio("Page", list(_PAGE_MODULES.keys()))` | Use `st.sidebar.radio` with `format_func` to prepend icons, or switch to `st.navigation` with `st.Page` objects |
| 25 | **Signal timestamps not human-friendly** — Timestamps are sliced ISO strings `[:19]` like `2026-03-21T14:30:00`. No relative time ("2 days ago") or formatted date. | `src/dashboard/views/signals.py` | 24 | `ts = d.get("timestamp", "")[:19]` | Parse with `datetime` and format as `"Mar 21, 2026 2:30 PM"` or add relative time |
| 26 | **Letter selectbox shows raw S3 keys** — `format_func` only strips `"letters/"` prefix, leaving filenames like `2026-Q1-owners-letter.md`. | `src/dashboard/views/letters.py` | 19 | `format_func=lambda k: k.replace("letters/", "")` | Parse the key to show `"Q1 2026 — Owner's Letter"` or similar human-readable label |
| 27 | **Login page is unstyled** — Default Streamlit input + button with no logo, no branding, and narrow layout (not `layout="wide"`). | `src/dashboard/app.py` | 50-61 | Default `st.text_input` + `st.button` in narrow layout | Add logo image, center the form with columns, use `st.form` for enter-key submission |
| 28 | **No `st.form` on login** — Password submission requires clicking the button. Pressing Enter doesn't submit because inputs aren't in a `st.form`. | `src/dashboard/app.py` | 53-54 | `st.text_input(...)` / `st.button("Login")` | Wrap in `with st.form("login"):` so Enter key triggers submission |
| 29 | **Screening Thresholds shown as raw JSON** — `st.json()` dumps the full config dict. Not scannable for a financial user. | `src/dashboard/views/feedback_loop.py` | 66 | `st.json(thresholds)` | Render as a formatted `st.dataframe` with columns: Metric, Current Value, Default Value |

## LOW

| # | Anti-Pattern | File | Line | Current Code | Recommended Fix |
|---|-------------|------|------|-------------|-----------------|
| 30 | **No custom CSS** — Zero `st.markdown(..., unsafe_allow_html=True)` calls. No fine-tuning of padding, margins, font sizes, or component spacing beyond Streamlit defaults. | All files | — | No CSS injection anywhere | Add a shared `inject_css()` utility for consistent spacing, card-like containers, and font refinements |
| 31 | **No empty-state illustrations** — All 9 empty states use identical `st.info("No X on record.")` pattern. Generic blue boxes instead of helpful empty-state guidance. | Multiple files | — | `st.info("No positions on record.")` etc. | Add contextual help: "No positions yet. Positions appear after the portfolio Lambda executes a BUY signal." |
| 32 | **Sidebar radio has no icons** — Page names are plain text. No visual differentiation between pages at a glance. | `src/dashboard/app.py` | 76 | `st.sidebar.radio("Page", list(_PAGE_MODULES.keys()))` | Prefix page names with icons: "📈 Portfolio Overview", "📋 Watchlist", "🔔 Signals", etc. |
| 33 | **No "last updated" timestamp** — Users can't tell if they're looking at fresh or stale data. No indication of when data was last fetched. | All view files | — | No timestamp display | Show `st.caption(f"Data as of {datetime.now():%H:%M:%S}")` at bottom of each page, or in sidebar |
| 34 | **Gain/Loss column is a formatted string** — Portfolio gains are pre-formatted as `"$1,234 (+5.6%)"`, making the column unsortable and un-filterable. | `src/dashboard/views/portfolio.py` | 45 | `f"${gain:,.0f} ({gain_pct:+.1f}%)"` | Split into two numeric columns ("Gain $", "Gain %") and use `column_config.NumberColumn` with format strings |
| 35 | **Cost Tracker doesn't show per-model breakdown** — Only total spend is shown. No visibility into which model tier (Opus/Sonnet/Haiku) is consuming budget. | `src/dashboard/views/cost_tracker.py` | 10-38 | Only aggregate `spent_usd` per month | If `CostTracker` stores per-model data, add a stacked bar chart or breakdown table |
| 36 | **No keyboard shortcuts or navigation hints** — No indication that sidebar radio can be used, no breadcrumb, no back button pattern. | `src/dashboard/app.py` | 64-78 | Radio-only navigation | Consider `st.navigation` API (Streamlit 1.36+) for native sidebar nav with URL-based routing |
| 37 | **Feedback Loop loads ALL data upfront** — Even if user only wants to see Lessons, all 4 sections (Lessons, Calibration, Thresholds, Postmortems) load simultaneously. | `src/dashboard/views/feedback_loop.py` | 15-94 | Sequential calls to all `load_*()` functions | If refactored to tabs, load each section's data only when the tab is selected (lazy loading) |

---

## Anti-Pattern Coverage Checklist

| # | Anti-Pattern Category | Issues Found | Severity Range |
|---|----------------------|-------------|---------------|
| 1 | Theming | 1 issue (#4) | HIGH |
| 2 | Visual Hierarchy | 3 issues (#10, #13, #29) | HIGH–MEDIUM |
| 3 | Layout | 2 issues (#12, #19) | HIGH–MEDIUM |
| 4 | Content Organization | 3 issues (#17, #18, #37) | MEDIUM–LOW |
| 5 | Data Display | 6 issues (#5, #6, #7, #8, #14, #34) | HIGH–LOW |
| 6 | KPI Presentation | 2 issues (#20, #21) | MEDIUM |
| 7 | Loading States | 3 issues (#1, #2, #3) | CRITICAL |
| 8 | Spacing / CSS | 2 issues (#16, #30) | MEDIUM–LOW |
| 9 | Page Config | 2 issues (#24, #27) | MEDIUM |
| 10 | Custom Styling | 1 issue (#30) | LOW |
| — | *Additional* | 12 issues | Various |
