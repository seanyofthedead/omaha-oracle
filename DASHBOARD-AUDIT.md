# Omaha Oracle — Streamlit Dashboard Architecture Audit

> Generated 2026-03-21. Read-only analysis — no code was modified.

## Overview

The dashboard is a 6-page Streamlit app with password authentication, sidebar navigation, and a centralized data layer. All charting uses Streamlit-native components (no Plotly, Altair, or Matplotlib). No CSS/HTML injection is used anywhere.

**Entry point:** `run_dashboard.py` launches `src/dashboard/app.py` via `streamlit.web.cli`

---

## Page Inventory

| Page | File | Purpose | `st.*` Components (with line numbers) |
|------|------|---------|---------------------------------------|
| *(Login)* | `src/dashboard/app.py:37-61` | Password gate before any page loads | `st.set_page_config` (:50), `st.title` (:51), `st.markdown` (:52), `st.text_input` (:53), `st.button` (:54), `st.error` (:60), `st.rerun` (:58), `st.stop` (:61) |
| Portfolio Overview | `src/dashboard/views/portfolio.py` | Account value, cash, and position table | `st.title` (:12), `st.columns(3)` (:19), `st.metric` (:21, :23, :26), `st.info` (:29), `st.dataframe` (:51) |
| Watchlist | `src/dashboard/views/watchlist.py` | Screened candidates with moat/IV scores | `st.title` (:12), `st.info` (:16), `st.dataframe` (:38) |
| Signals | `src/dashboard/views/signals.py` | Recent BUY/SELL decisions with reasoning | `st.title` (:12), `st.sidebar.slider` (:14), `st.info` (:18), `st.subheader` (:28), `st.write` (:38), `st.caption` (:40), `st.divider` (:42) |
| Cost Tracker | `src/dashboard/views/cost_tracker.py` | Monthly LLM spend vs. budget | `st.title` (:12), `st.sidebar.slider` (:14), `st.columns(4)` (:17), `st.metric` (:19, :21, :23, :26), `st.warning` (:29), `st.bar_chart` (:36), `st.info` (:38) |
| Owner's Letters | `src/dashboard/views/letters.py` | Quarterly Buffett-style post-mortem letters | `st.title` (:12), `st.info` (:16), `st.selectbox` (:19), `st.markdown` (:22) |
| Feedback Loop | `src/dashboard/views/feedback_loop.py` | Active lessons, calibration, thresholds, mistake trends | `st.title` (:17), `st.subheader` (:20, :44, :63, :71), `st.dataframe` (:39, :58), `st.info` (:41, :60, :68, :92, :94), `st.json` (:66), `st.line_chart` (:90) |

---

## Data Flow Map

All views import from `src/dashboard/data.py`. Views never call AWS services directly.

```
View Page              data.py function              AWS Resource
─────────────────────  ────────────────────────────  ──────────────────────────────────
Portfolio Overview  →  load_portfolio()           →  DynamoDB: table_portfolio
Watchlist           →  load_watchlist_analysis()  →  DynamoDB: table_watchlist + table_analysis
Signals             →  load_decisions()           →  DynamoDB: table_decisions (record_type-timestamp-index)
Cost Tracker        →  load_cost_data()           →  DynamoDB: table_cost_tracking (via CostTracker)
Owner's Letters     →  load_letter_keys()         →  S3: letters/ prefix
                       load_letter_content()      →  S3: letters/{key}
Feedback Loop       →  load_lessons()             →  DynamoDB: table_lessons (active_flag-expires_at-index)
                       load_postmortem_keys()     →  S3: postmortems/ prefix
                       load_postmortem()          →  S3: postmortems/{key}
                       load_config_thresholds()   →  DynamoDB: table_config
```

### Data Layer Details (`src/dashboard/data.py`)

| Function | Lines | Returns | Concurrency |
|----------|-------|---------|-------------|
| `load_portfolio()` | 23-36 | `{cash, portfolio_value, positions[]}` | — |
| `load_watchlist_analysis()` | 39-89 | List of candidates with merged analysis | ThreadPoolExecutor (10 workers) |
| `load_decisions()` | 92-106 | List of decision records | — |
| `load_cost_data()` | 109-139 | `(history[], status{})` | ThreadPoolExecutor (2 workers) |
| `load_letter_keys()` | 142-151 | Sorted list of S3 keys | — |
| `load_letter_content()` | 154-162 | Markdown string | — |
| `load_postmortem_keys()` | 165-174 | Sorted list of S3 keys | — |
| `load_postmortem()` | 177-185 | Dict | — |
| `load_lessons()` | 188-202 | List of active lessons | — |
| `load_config_thresholds()` | 205-214 | Dict of thresholds | — |

All functions return empty/zero defaults on exception. No Streamlit caching decorators are used.

---

## Navigation Structure

```
run_dashboard.py
  └─ streamlit.web.cli.main() launches src/dashboard/app.py

app.py:main()
  ├─ _require_auth()                          # Password gate (lines 37-61)
  │   ├─ Checks st.session_state.authenticated
  │   ├─ Shows login form (st.text_input + st.button)
  │   ├─ Validates against st.secrets["dashboard_password"]
  │   └─ st.stop() blocks further rendering until authenticated
  │
  ├─ st.set_page_config(layout="wide")       # Line 67
  │
  ├─ st.sidebar.title("Omaha Oracle")        # Line 73
  ├─ st.sidebar.radio("Page", [...])         # Line 76 — 6 page options
  │
  └─ importlib.import_module(page).render()  # Line 77 — dynamic dispatch
```

**Page registry** (`app.py:27-34`):
```python
_PAGE_MODULES = {
    "Portfolio Overview": "dashboard.views.portfolio",
    "Watchlist":          "dashboard.views.watchlist",
    "Signals":            "dashboard.views.signals",
    "Cost Tracker":       "dashboard.views.cost_tracker",
    "Owner's Letters":    "dashboard.views.letters",
    "Feedback Loop":      "dashboard.views.feedback_loop",
}
```

Navigation is flat — all pages are siblings accessed via the sidebar radio. There are no sub-pages or inter-page links.

---

## Shared Dependencies

### Modules imported by multiple dashboard files

| Module | Imported By | Purpose |
|--------|------------|---------|
| `streamlit` | `app.py`, all 6 view files | UI framework |
| `dashboard.data` | All 6 view files | Centralized data loading |
| `pandas` | `views/cost_tracker.py:32`, `views/feedback_loop.py:76` | DataFrame construction for charts |

### Shared modules from `src/shared/` (imported by `data.py`)

| Module | Import Location (`data.py`) | Purpose |
|--------|----------------------------|---------|
| `shared.config.get_config` | Line 13 | Resolves table names, S3 bucket, budget settings |
| `shared.dynamo_client.DynamoClient` | Line 15 | Generic DynamoDB CRUD wrapper |
| `shared.s3_client.S3Client` | Line 18 | S3 read (JSON, Markdown) + list keys |
| `shared.cost_tracker.CostTracker` | Line 14 | LLM spend tracking + budget enforcement |
| `shared.portfolio_helpers.load_portfolio_state` | Line 17 | Loads account summary + positions |
| `shared.analysis_client.merge_latest_analysis` | Line 12 | Merges analysis records by date for a ticker |
| `shared.logger.get_logger` | Line 16 | Structured logging |
| `boto3.dynamodb.conditions.Key` | Line 10 | DynamoDB query key conditions |

### Configuration files

| File | Purpose |
|------|---------|
| `.streamlit/config.toml` | CORS, XSRF (SameSite=Strict), headless mode |
| `.streamlit/secrets.toml` | Dashboard password (gitignored) |
| `.streamlit/secrets.toml.example` | Template for secrets |

---

## Component Census

Total count of each `st.*` component type across the entire dashboard:

| Component | Count | Used In |
|-----------|-------|---------|
| `st.title` | 7 | app.py, all 6 views |
| `st.info` | 9 | portfolio, watchlist, signals, cost_tracker, letters, feedback_loop (x4) |
| `st.metric` | 7 | portfolio (x3), cost_tracker (x4) |
| `st.dataframe` | 4 | portfolio, watchlist, feedback_loop (x2) |
| `st.subheader` | 5 | signals, feedback_loop (x4) |
| `st.columns` | 2 | portfolio (3-col), cost_tracker (4-col) |
| `st.markdown` | 3 | app.py (x2), letters |
| `st.sidebar.slider` | 2 | signals, cost_tracker |
| `st.sidebar.radio` | 1 | app.py |
| `st.sidebar.title` | 1 | app.py |
| `st.sidebar.markdown` | 1 | app.py |
| `st.set_page_config` | 2 | app.py (login + main) |
| `st.selectbox` | 1 | letters |
| `st.bar_chart` | 1 | cost_tracker |
| `st.line_chart` | 1 | feedback_loop |
| `st.json` | 1 | feedback_loop |
| `st.divider` | 1 | signals |
| `st.caption` | 1 | signals |
| `st.write` | 1 | signals |
| `st.warning` | 1 | cost_tracker |
| `st.error` | 1 | app.py |
| `st.button` | 1 | app.py |
| `st.text_input` | 1 | app.py |
| `st.rerun` | 1 | app.py |
| `st.stop` | 1 | app.py |
| `st.session_state` | 2 | app.py |
| `st.secrets` | 1 | app.py |
| **Total** | **58** | |

### Charting Library Usage

| Library | Component | Page | Line |
|---------|-----------|------|------|
| Streamlit native | `st.bar_chart` | Cost Tracker | `cost_tracker.py:36` |
| Streamlit native | `st.line_chart` | Feedback Loop | `feedback_loop.py:90` |
| pandas | `pd.DataFrame` (for chart data) | Cost Tracker | `cost_tracker.py:34` |
| pandas | `pd.DataFrame` (for chart data) | Feedback Loop | `feedback_loop.py:89` |

No Plotly, Altair, or Matplotlib is used anywhere in the dashboard.

---

## Notable Architectural Observations

1. **No caching** — Every page render triggers fresh AWS calls. Adding `@st.cache_data(ttl=60)` to `data.py` functions would reduce latency and cost.
2. **Graceful degradation** — All `data.py` functions catch exceptions and return empty defaults, so the dashboard renders even without AWS connectivity.
3. **Concurrency** — `ThreadPoolExecutor` is used in 3 places to parallelize AWS queries (watchlist analysis, cost data, postmortem loading).
4. **Flat navigation** — No sub-pages, tabs, or inter-page deep links. All routing goes through one sidebar radio.
5. **Auth is session-only** — Password stored in `st.secrets`, checked against `st.session_state`. No role-based access or token expiry.
