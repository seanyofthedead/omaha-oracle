# Omaha Oracle — Dashboard Performance Audit

> Generated 2026-03-21. Read-only audit — no code was modified.

## Findings Summary

| Impact | Count |
|--------|-------|
| PAGE_FREEZE | 5 |
| SLOW_LOAD | 14 |
| FLICKER | 1 |
| MEMORY_BLOAT | 4 |
| **Total** | **24** |

---

## 1. Uncached Data-Loading Functions

Every `load_*()` function in `data.py` lacks `@st.cache_data`. Each Streamlit rerun (any widget interaction) re-executes ALL data-loading calls for the active page from scratch — full DynamoDB queries, S3 reads, and object construction.

| # | File | Line | Function | AWS Calls Per Rerun | Est. Latency | Impact | Recommended TTL |
|---|------|------|----------|-------------------|-------------|--------|-----------------|
| 1 | `data.py` | 23 | `load_portfolio()` | 2 DynamoDB calls (get_item + query) | ~200-400ms | SLOW_LOAD | `@st.cache_data(ttl=30)` — portfolio changes only on trade execution |
| 2 | `data.py` | 39 | `load_watchlist_analysis()` | 1 scan + N queries (1 per ticker, up to 10 parallel) | ~1-3s for 20+ tickers | PAGE_FREEZE | `@st.cache_data(ttl=300)` — analysis runs daily, 5min cache is fine |
| 3 | `data.py` | 92 | `load_decisions()` | 1 DynamoDB index query | ~150-300ms | SLOW_LOAD | `@st.cache_data(ttl=60)` — decisions change infrequently |
| 4 | `data.py` | 109 | `load_cost_data()` | N+1 DynamoDB queries (1 per month + 1 budget check) | ~1-4s for 12 months | PAGE_FREEZE | `@st.cache_data(ttl=120)` — cost data changes only during pipeline runs |
| 5 | `data.py` | 142 | `load_letter_keys()` | 1 S3 list_objects_v2 | ~100-200ms | SLOW_LOAD | `@st.cache_data(ttl=3600)` — letters publish quarterly |
| 6 | `data.py` | 154 | `load_letter_content()` | 1 S3 get_object | ~100-300ms | SLOW_LOAD | `@st.cache_data(ttl=3600)` — content is immutable once published |
| 7 | `data.py` | 165 | `load_postmortem_keys()` | 1 S3 list_objects_v2 | ~100-200ms | SLOW_LOAD | `@st.cache_data(ttl=3600)` — postmortems publish quarterly |
| 8 | `data.py` | 177 | `load_postmortem()` | 1 S3 get_object | ~100-300ms | SLOW_LOAD | `@st.cache_data(ttl=3600)` — content is immutable once published |
| 9 | `data.py` | 188 | `load_lessons()` | 1 DynamoDB index query | ~150-300ms | SLOW_LOAD | `@st.cache_data(ttl=300)` — lessons update on quarterly post-mortem |
| 10 | `data.py` | 205 | `load_config_thresholds()` | 1 DynamoDB get_item | ~50-150ms | SLOW_LOAD | `@st.cache_data(ttl=300)` — thresholds update quarterly |

**Aggregate impact:** The Feedback Loop page calls 4 uncached functions sequentially + loads up to 12 postmortems in parallel. Worst case: **~4-8 seconds per rerun** with zero caching.

---

## 2. Expensive Object Instantiation on Every Call

Each `load_*()` function creates new boto3 clients/resources on every invocation. These involve HTTP connection setup, credential resolution, and endpoint discovery.

| # | File | Line | Object Created | What It Does | Est. Overhead | Impact | Fix |
|---|------|------|---------------|-------------|--------------|--------|-----|
| 11 | `data.py` | 27 | `load_portfolio_state()` → `DynamoClient(table)` | Creates `boto3.resource("dynamodb").Table()` | ~50-100ms (TCP + TLS + credential chain) | SLOW_LOAD | Extract client creation to a `@st.cache_resource` factory |
| 12 | `data.py` | 45-46 | `DynamoClient(cfg.table_watchlist)` + `DynamoClient(cfg.table_analysis)` | Two separate boto3 resource + Table() calls | ~100-200ms combined | SLOW_LOAD | Cache clients via `@st.cache_resource` |
| 13 | `data.py` | 96 | `DynamoClient(cfg.table_decisions)` | New boto3 resource + Table() | ~50-100ms | SLOW_LOAD | Cache client via `@st.cache_resource` |
| 14 | `data.py` | 112 | `CostTracker()` | Creates `boto3.resource("dynamodb").Table()` + reads config | ~50-100ms | SLOW_LOAD | Cache via `@st.cache_resource` |
| 15 | `data.py` | 146, 158 | `S3Client(bucket=cfg.s3_bucket)` | Creates `boto3.client("s3")` — called in BOTH `load_letter_keys()` and `load_letter_content()` | ~50-100ms each, 2x per letters page | SLOW_LOAD | Single cached S3Client via `@st.cache_resource` |
| 16 | `data.py` | 169, 181 | `S3Client(bucket=cfg.s3_bucket)` | Two more S3 client instantiations for postmortem functions | ~50-100ms each | SLOW_LOAD | Same cached S3Client |
| 17 | `data.py` | 194, 209 | `DynamoClient(cfg.table_lessons)` + `DynamoClient(cfg.table_config)` | Two more boto3 resource + Table() | ~100-200ms combined | SLOW_LOAD | Cache clients |

**Aggregate impact:** The Feedback Loop page creates **4 DynamoClients + 2 S3Clients = 6 boto3 sessions** per rerun. With caching, this drops to zero after the first load.

**Recommended fix — single factory function:**
```python
@st.cache_resource
def _get_dynamo_client(table_name: str) -> DynamoClient:
    return DynamoClient(table_name)

@st.cache_resource
def _get_s3_client() -> S3Client:
    return S3Client(bucket=get_config().s3_bucket)
```

---

## 3. Blocking Operations Without Loading Indicators

| # | File | Line | Operation | Duration | Impact | Fix |
|---|------|------|-----------|----------|--------|-----|
| 18 | `views/portfolio.py` | 14 | `load_portfolio()` — blocks while DynamoDB returns | ~200-400ms | PAGE_FREEZE | `with st.spinner("Loading portfolio..."):` |
| 19 | `views/watchlist.py` | 14 | `load_watchlist_analysis()` — ThreadPoolExecutor with up to 10 DynamoDB queries | ~1-3s | PAGE_FREEZE | `with st.status("Analyzing watchlist...", expanded=True) as s:` with step updates |
| 20 | `views/cost_tracker.py` | 15 | `load_cost_data(months=...)` — up to 24 monthly queries | ~2-4s at max slider | PAGE_FREEZE | `with st.spinner("Loading cost data..."):` |
| 21 | `views/feedback_loop.py` | 21+64+72 | Three sequential `load_*()` calls + ThreadPoolExecutor for postmortems | ~3-6s total | PAGE_FREEZE | `with st.status("Loading feedback data...") as s:` with `s.update()` per section |
| 22 | `views/letters.py` | 21 | `load_letter_content(selected)` — S3 read triggered by selectbox change | ~100-300ms | SLOW_LOAD | `with st.spinner("Loading letter..."):` |

---

## 4. Unnecessary `st.rerun()` Call

| # | File | Line | Code | Impact | Fix |
|---|------|------|------|--------|-----|
| 23 | `app.py` | 58 | `st.rerun()` after setting `st.session_state.authenticated = True` | FLICKER | This is actually necessary for the auth flow to work — the rerun clears the login form and renders the main app. However, it causes a visible flash. **Mitigate** by using `st.fragment` or `st.form` with `on_submit` callback to avoid the full-page rerun. |

---

## 5. Unbounded Data Rendering

| # | File | Line | Issue | Impact | Fix |
|---|------|------|-------|--------|-----|
| 24 | `views/feedback_loop.py` | 78-80 | `ThreadPoolExecutor(max_workers=len(batch))` where `batch = keys[:12]` — spawns up to **12 threads** per page load to fetch postmortems. Each thread creates its own S3Client (boto3 session). | MEMORY_BLOAT | Cap workers: `max_workers=min(len(batch), 4)`. With caching this entire block runs once. |

---

## 6. Cost Tracker `get_spend_history` Is Serially Sequential

| # | File | Line | Issue | Impact | Fix |
|---|------|------|-------|--------|-----|
| 25 | `cost_tracker.py` | 243-265 | `get_spend_history()` iterates month keys **sequentially**, issuing one DynamoDB query per month. For 24 months, this is 24 serial round-trips. | SLOW_LOAD | Parallelize with ThreadPoolExecutor: query all months concurrently. Or use `@st.cache_data(ttl=120)` on `load_cost_data()` so this only runs once. |

---

## Page-Level Performance Summary

| Page | Data Functions Called | Est. Uncached Load | Est. Cached Load | Worst Bottleneck |
|------|---------------------|-------------------|-----------------|------------------|
| Portfolio Overview | `load_portfolio()` | ~400ms | <50ms | DynamoClient instantiation |
| Watchlist | `load_watchlist_analysis()` | ~1-3s | <50ms | N parallel DynamoDB queries |
| Signals | `load_decisions()` | ~300ms | <50ms | DynamoClient instantiation |
| Cost Tracker | `load_cost_data()` | ~2-4s (12mo) / ~6-8s (24mo) | <50ms | Sequential month-by-month queries |
| Owner's Letters | `load_letter_keys()` + `load_letter_content()` | ~300-500ms | <50ms | Two S3Client instantiations |
| Feedback Loop | `load_lessons()` + `load_config_thresholds()` + `load_postmortem_keys()` + N×`load_postmortem()` | ~4-8s | <50ms | 12 parallel S3 reads + 4 client instantiations |

---

## Priority Fix Order

**Phase 1 — Maximum impact, minimal code change:**
1. Add `@st.cache_data(ttl=...)` to all 10 `load_*()` functions in `data.py` (fixes #1-10)
2. Add `@st.cache_resource` factory for DynamoClient and S3Client (fixes #11-17)

**Phase 2 — User experience:**
3. Add `st.spinner()` or `st.status()` wrappers in all 6 view `render()` functions (fixes #18-22)

**Phase 3 — Structural improvements:**
4. Cap ThreadPoolExecutor workers in `feedback_loop.py` (fixes #24)
5. Parallelize `get_spend_history()` in `cost_tracker.py` (fixes #25)
6. Add a sidebar "Refresh" button that calls `st.cache_data.clear()` for manual cache invalidation

**After all fixes:** Every cached page rerun should complete in <50ms. First loads (cold cache) should show spinners and complete within 1-3s.
