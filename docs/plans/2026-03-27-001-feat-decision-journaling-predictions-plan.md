---
title: "feat: Add decision journaling with falsifiable predictions"
type: feat
status: completed
date: 2026-03-27
origin: docs/brainstorms/2026-03-27-decision-journaling-requirements.md
---

# feat: Add Decision Journaling with Falsifiable Predictions

## Overview

Add structured, machine-evaluable predictions to every BUY thesis. Predictions are auto-evaluated weekly, generating micro-lessons from failures without LLM cost, and feeding prediction accuracy into the existing confidence calibration system. This transforms the self-improvement loop from retrospective narrative guessing into ground-truth signal.

## Problem Frame

The system's self-improvement loop relies on quarterly post-mortems where the LLM guesses what went wrong. The thesis prompt already generates a "Kill the Thesis" section with failure scenarios, but this is unstructured markdown stored in S3 — never machine-evaluated. Pre-committed measurable predictions close this gap. (see origin: docs/brainstorms/2026-03-27-decision-journaling-requirements.md)

## Requirements Trace

- R1. Structured predictions on every BUY thesis — 3 falsifiable predictions with metric name, threshold, operator, data source, deadline
- R2. Predictions stored in decisions table payload as a `predictions` array
- R3. Automated weekly evaluation — scan matured predictions, fetch actuals, classify CONFIRMED/FALSIFIED
- R4. Auto-extracted micro-lessons from falsified predictions — no LLM call
- R5. Prediction accuracy feeds into confidence calibration per (analysis_stage, sector)
- R6. Dashboard view showing active predictions grouped by ticker with status

## Scope Boundaries

- BUY decisions only — no predictions on SELL, HOLD, NO_BUY (see origin)
- No LLM call for evaluation — purely numeric comparison (see origin)
- No new data sources — predictions constrained to metrics from existing ingestion (see origin)
- No changes to thesis markdown format — "Kill the Thesis" section persists as prose (see origin)
- Prediction evaluation does not trigger trades (see origin)

## Context & Research

### Relevant Code and Patterns

- `src/analysis/thesis_generator/handler.py` — Current thesis generation: single `invoke()` with `tier="thesis"`, `require_json=False`, outputs raw markdown to S3
- `src/portfolio/allocation/handler.py` — `_log_decision()` writes to decisions table with `payload` dict; BUY payload includes signal, reasons, position_size
- `src/monitoring/owners_letter/audit.py` — `run_outcome_audit()`: queries decisions via GSI, batch-fetches prices via yfinance, classifies by return thresholds
- `src/shared/lessons_client.py` — `get_confidence_adjustment()`: queries `lesson_type="confidence_calibration"`, filters by stage/sector, geometric mean of adjustment_factors, clamped [0.5, 1.5]
- `src/monitoring/owners_letter/pipeline.py` — `extract_lessons()`: lesson schema with `active_flag`, `confidence_calibration` map, `expires_at`
- `src/shared/llm_client.py` — `invoke()` supports `require_json=True` which appends JSON instruction and parses response
- `src/shared/dynamo_client.py` — `DynamoClient` with auto float-to-Decimal sanitization (use for all new DynamoDB writes)
- `infra/stacks/monitoring_stack.py` — `_fn()` factory + EventBridge cron pattern for scheduled Lambdas
- `src/dashboard/views/portfolio.py` — View pattern: `render()` function, `@st.cache_data(ttl=N)` data fetching

### Institutional Learnings

- **GSI deployment lag** (commit `e1a062e`): New GSIs on existing tables may not be available immediately after CDK deploy. Dashboard and Lambda code must handle `ValidationException` with fallback to scan + client-side filter.
- **DynamoDB `active_flag` is a string**: Must be `"1"` not `True` for the GSI query in lessons_client to work.
- **Lesson schema compliance is critical**: Auto-generated lessons must include all required fields from `pipeline.py` lines 146-165, especially `active_flag`, `expires_at`, and the `confidence_calibration` nested map structure.

## Key Technical Decisions

- **Two-pass thesis generation** (resolves deferred Q1): First call produces markdown (existing, unchanged). Second call with `tier="bulk"` (Haiku) and `require_json=True` extracts 3 structured predictions from the thesis text. This avoids changing the existing thesis call and uses the cheapest model for structured extraction. If `BudgetExhaustedError` occurs on the extraction call, store the thesis without predictions rather than failing.

- **Allowed metrics constrained to existing ingestion** (resolves deferred Q2): Predictions are constrained to metrics the system can look up from Yahoo Finance and SEC EDGAR data already stored in the companies and financials tables:
  - `revenue`, `earnings_per_share`, `gross_margin`, `operating_margin`, `net_margin`
  - `book_value_per_share`, `debt_to_equity`, `free_cash_flow`, `return_on_equity`
  - `stock_price` (from companies table or yfinance)
  The prediction extraction prompt must list these exact metric names and reject any prediction using an unlisted metric.

- **New Lambda in MonitoringStack** (resolves deferred Q3): The prediction evaluator is a new Lambda, not an extension of the owners' letter or cost monitor. It has a distinct cadence (weekly) and distinct data access pattern (decisions + financials + lessons tables). Scheduled Wednesday 06:00 UTC to avoid contention with the owners' letter (Sunday).

- **Metric-to-stage mapping table** (resolves deferred Q4):
  - `revenue`, `earnings_per_share`, `free_cash_flow`, `stock_price` → `intrinsic_value`
  - `gross_margin`, `operating_margin`, `net_margin`, `return_on_equity` → `moat_analysis`
  - `book_value_per_share`, `debt_to_equity` → `quant_screen`
  This mapping is a constant dict in the evaluator module.

- **Minimum sample size of 10** (resolves deferred Q5): Prediction accuracy does not influence confidence calibration until ≥ 10 predictions have been evaluated for a given (stage, sector) pair. Below that, only accuracy stats are tracked — no calibration lessons are written. This prevents a single falsified prediction from causing a 0.5x confidence drop.

- **No new DynamoDB table**: Predictions co-locate in the decisions table payload as a `predictions` array attribute. This is appropriate for BUY-only, low-volume data. The weekly evaluator queries via the existing `record_type-timestamp-index` GSI and filters client-side for items with pending predictions.

## Open Questions

### Resolved During Planning

- **Q1: How to output predictions alongside markdown?** Two-pass: thesis markdown first (unchanged), then Haiku extraction call with `require_json=True`.
- **Q2: What metrics are predictable?** 10 metrics from Yahoo Finance + SEC EDGAR already ingested. Prompt constrains to this exact list.
- **Q3: New Lambda or extend existing?** New Lambda in MonitoringStack, weekly Wednesday 06:00 UTC.
- **Q4: Metric-to-stage mapping?** Static dict mapping 10 metrics to 3 analysis stages.
- **Q5: Minimum sample size for calibration?** 10 predictions per (stage, sector) pair.

### Deferred to Implementation

- **Exact metric lookup functions**: How to fetch each metric's actual value from the companies/financials tables depends on the current column names and data freshness. Implementer should inspect the table schemas at implementation time.
- **Prediction prompt wording**: The exact prompt for the Haiku extraction call should be iterated during implementation with test cases.
- **Dashboard layout details**: Exact column layout, chart types, and filter options for the decision journal view.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
┌─────────────────────────────────────────────────────────────┐
│                    THESIS GENERATION (Step Functions)        │
│                                                             │
│  1. Existing flow: invoke(tier="thesis") → markdown         │
│  2. NEW: invoke(tier="bulk", require_json=True)             │
│     Input: thesis markdown + allowed metrics list           │
│     Output: { "predictions": [                              │
│       { "id": "pred_..._0",                                 │
│         "description": "Revenue exceeds $400B by Q4 2026",  │
│         "metric": "revenue",                                │
│         "operator": ">",                                    │
│         "threshold": 400000000000,                          │
│         "data_source": "yahoo_finance",                     │
│         "deadline": "2026-12-31",                           │
│         "analysis_stage": "intrinsic_value"  ← auto-mapped  │
│       }, ... ] }                                            │
│  3. Predictions returned in handler output dict             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              ALLOCATION HANDLER (portfolio)                  │
│                                                             │
│  _log_decision() payload now includes:                      │
│    "predictions": [ { ...prediction objects with            │
│      "status": "pending" added } ]                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│           DECISIONS TABLE (DynamoDB)                         │
│                                                             │
│  Existing: decision_id, timestamp, ticker, signal, payload  │
│  payload.predictions = [ { id, description, metric,         │
│    operator, threshold, data_source, deadline, status,       │
│    analysis_stage } ]                                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
              (weekly EventBridge trigger)
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│          PREDICTION EVALUATOR (new Lambda)                   │
│                                                             │
│  1. Query decisions table via GSI for DECISION records      │
│  2. Filter for items with predictions[].status == "pending" │
│     and predictions[].deadline <= now                       │
│  3. For each matured prediction:                            │
│     a. Fetch actual metric from companies/financials table  │
│     b. Compare actual vs threshold using operator           │
│     c. Classify: CONFIRMED or FALSIFIED                     │
│     d. Update prediction status in decisions table          │
│  4. For each FALSIFIED prediction:                          │
│     a. Generate micro-lesson (no LLM) conforming to        │
│        lesson schema with confidence_calibration map        │
│     b. Write to lessons table                               │
│  5. Track accuracy per (stage, sector)                      │
│  6. If sample_size >= 10 for a (stage, sector) pair:        │
│     Write/update confidence_calibration lesson with         │
│     adjustment_factor derived from accuracy rate            │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Units

- [x] **Unit 1: Prediction extraction in thesis generator**

  **Goal:** After generating the thesis markdown, extract 3 structured falsifiable predictions via a second LLM call.

  **Requirements:** R1

  **Dependencies:** None

  **Files:**
  - Modify: `src/analysis/thesis_generator/handler.py`
  - Create: `prompts/prediction_extraction.md`
  - Test: `tests/unit/analysis/test_thesis_generator.py`

  **Approach:**
  - After the existing `invoke(tier="thesis")` call, add a second `invoke(tier="bulk", require_json=True)` call
  - The prediction extraction prompt receives the thesis markdown and the `ALLOWED_METRICS` list, and must return exactly 3 predictions in a defined JSON schema
  - Each prediction gets an `analysis_stage` field auto-mapped from the metric name using a constant `METRIC_TO_STAGE` dict
  - Handle `BudgetExhaustedError`: if the extraction call fails due to budget, log a warning and continue with the thesis but no predictions (set `predictions=[]` in the output)
  - Return predictions in the handler's output dict (e.g., `result["predictions"] = [...]`)

  **Patterns to follow:**
  - `src/analysis/moat_analysis/handler.py` — uses `require_json=True` for structured LLM output
  - `src/analysis/thesis_generator/handler.py` — existing handler structure for the first pass

  **Test scenarios:**
  - Happy path: mock LLM returns valid 3-prediction JSON → predictions included in output
  - Budget exhaustion on extraction call → thesis stored without predictions, no error raised
  - LLM returns invalid JSON → graceful fallback to empty predictions
  - LLM returns prediction with disallowed metric → rejected/filtered
  - Thesis gate conditions not met (moat < 7, etc.) → handler exits early, no prediction call made

  **Verification:**
  - Handler output includes `predictions` key with list of 0-3 prediction dicts
  - Each prediction has all required fields: id, description, metric, operator, threshold, data_source, deadline, analysis_stage
  - All metrics are in the ALLOWED_METRICS set
  - Cost tracking shows two LLM calls when predictions are generated (thesis + extraction)

- [x] **Unit 2: Thread predictions through to decisions table**

  **Goal:** Carry predictions from the thesis generator output through the allocation handler into the decisions table payload.

  **Requirements:** R2

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `src/portfolio/allocation/handler.py`
  - Test: `tests/unit/portfolio/test_allocation_handler.py`

  **Approach:**
  - The allocation handler receives analysis data including thesis output. Read the `predictions` array from the thesis result
  - In `_log_decision()`, add `"predictions"` to the BUY payload, with each prediction augmented with `"status": "pending"`
  - Generate prediction IDs: `pred_{decision_id}_{index}` using the decision_id from the same `_log_decision` call
  - If no predictions exist (budget exhaustion case), write `"predictions": []`

  **Patterns to follow:**
  - Existing `_log_decision()` in `src/portfolio/allocation/handler.py` — payload construction pattern
  - `DynamoClient.put_item()` handles float-to-Decimal conversion automatically

  **Test scenarios:**
  - BUY decision with 3 predictions → all stored in payload with status "pending"
  - BUY decision with empty predictions (budget exhaustion) → `predictions: []` in payload
  - SELL/HOLD/NO_BUY decisions → no predictions field added
  - Prediction IDs are correctly derived from decision_id

  **Verification:**
  - DynamoDB decision items for BUY signals contain a `predictions` array in payload
  - Each prediction has `status: "pending"` and a unique `id`

- [x] **Unit 3: Prediction evaluator core logic**

  **Goal:** Create the evaluation engine that fetches matured predictions, looks up actual metric values, and classifies outcomes.

  **Requirements:** R3

  **Dependencies:** Unit 2

  **Files:**
  - Create: `src/monitoring/prediction_evaluator/__init__.py`
  - Create: `src/monitoring/prediction_evaluator/evaluator.py`
  - Create: `src/monitoring/prediction_evaluator/metrics.py`
  - Test: `tests/unit/monitoring/test_prediction_evaluator.py`

  **Approach:**
  - `evaluator.py` contains the core `evaluate_matured_predictions()` function:
    1. Query decisions table via GSI (`record_type="DECISION"`) for all decisions
    2. Filter client-side for items with `payload.predictions` containing entries where `status == "pending"` and `deadline <= now`
    3. For each matured prediction, call `metrics.fetch_actual(metric_name, ticker, data_source)` to get the current value
    4. Compare actual vs threshold using the operator. Classify as `CONFIRMED` or `FALSIFIED`
    5. Update the prediction's `status` and `actual_value` in the decisions table via `update_item`
    6. Return list of evaluated predictions with outcomes
  - `metrics.py` contains `fetch_actual()` which dispatches to the correct data source:
    - `yahoo_finance` metrics: read from companies table (already ingested)
    - `sec_edgar` metrics: read from financials table (already ingested)
    - If metric is unavailable, classify as `UNRESOLVABLE` (not FALSIFIED)
  - `METRIC_TO_STAGE` mapping dict and `ALLOWED_METRICS` set live here as constants shared with the thesis generator

  **Patterns to follow:**
  - `src/monitoring/owners_letter/audit.py` — `run_outcome_audit()` pattern of querying decisions, fetching data, classifying
  - `src/shared/dynamo_client.py` — `DynamoClient` for table access

  **Test scenarios:**
  - Matured prediction with actual > threshold and operator ">" → CONFIRMED
  - Matured prediction with actual < threshold and operator ">" → FALSIFIED
  - Prediction deadline not yet passed → skipped
  - Prediction already evaluated (status != "pending") → skipped
  - Metric not available in data source → UNRESOLVABLE
  - Multiple predictions on same decision, mixed outcomes
  - Empty decisions table → no-op
  - Decision with no predictions field (pre-feature decisions) → skipped gracefully

  **Verification:**
  - All matured pending predictions are evaluated
  - Decision items in DynamoDB are updated with status and actual_value
  - Pre-existing decisions without predictions field are handled without error

- [x] **Unit 4: Auto-lesson generation from falsified predictions**

  **Goal:** Generate structured lessons from FALSIFIED predictions without LLM calls, conforming to the existing lesson schema.

  **Requirements:** R4, R5

  **Dependencies:** Unit 3

  **Files:**
  - Create: `src/monitoring/prediction_evaluator/lesson_generator.py`
  - Modify: `src/monitoring/prediction_evaluator/evaluator.py` (call lesson generator after evaluation)
  - Test: `tests/unit/monitoring/test_prediction_lesson_generator.py`

  **Approach:**
  - For each FALSIFIED prediction, generate a lesson dict conforming exactly to the schema in `pipeline.py` lines 146-165:
    - `lesson_type`: `"prediction_miss"` (new type, distinct from existing types)
    - `lesson_id`: `"PRED_{ticker}_{prediction_id}"`
    - `severity`: derived from miss magnitude (e.g., >50% miss → "high", >20% → "moderate", else "minor")
    - `description`: templated string: "Predicted {metric} {operator} {threshold} by {deadline} for {ticker}. Actual: {actual_value}."
    - `actionable_rule`: templated: "When analyzing {sector} companies, reduce confidence in {metric} predictions. Historical miss rate: {miss_magnitude}%."
    - `prompt_injection_text`: templated: "CAUTION: Past {metric} predictions for {sector} stocks have been inaccurate. The system predicted {metric} {operator} {threshold} for {ticker} but actual was {actual_value}. Weight {metric}-based assumptions conservatively."
    - `ticker`, `sector`: from the decision record
    - `active_flag`: `"1"` (string, not boolean)
    - `expires_at`: 4 quarters from now
    - `confidence_calibration`: only written when sample size ≥ 10 for (stage, sector)
  - Track prediction accuracy per (analysis_stage, sector) pair. When sample size reaches 10, write/update a `lesson_type="confidence_calibration"` lesson:
    - `adjustment_factor`: `accuracy_rate` clamped to [0.5, 1.3] (lower bound prevents complete suppression, upper bound below 1.5 to avoid over-boosting from small positive streaks)
  - Use `DynamoClient` for all writes, ensuring float-to-Decimal sanitization

  **Patterns to follow:**
  - `src/monitoring/owners_letter/pipeline.py` `extract_lessons()` — lesson schema and DynamoDB write pattern
  - `src/shared/lessons_client.py` — `confidence_calibration` map structure expected by `get_confidence_adjustment()`

  **Test scenarios:**
  - FALSIFIED prediction with 50%+ miss → lesson with severity "high"
  - FALSIFIED prediction with 25% miss → lesson with severity "moderate"
  - CONFIRMED prediction → no lesson generated
  - UNRESOLVABLE prediction → no lesson generated
  - Lesson schema validation: all required fields present, `active_flag` is string `"1"`, `expires_at` is valid ISO timestamp
  - Sample size < 10 → no confidence_calibration lesson written
  - Sample size reaches 10 → confidence_calibration lesson written with correct adjustment_factor
  - Accuracy rate 100% → adjustment_factor capped at 1.3
  - Accuracy rate 0% → adjustment_factor capped at 0.5

  **Verification:**
  - Lessons table contains entries for every FALSIFIED prediction
  - Lesson records are queryable by `LessonsClient.get_relevant_lessons()` (correct `lesson_type`, `active_flag`, `expires_at`)
  - Confidence calibration lessons appear only after 10+ predictions for a (stage, sector) pair
  - `LessonsClient.get_confidence_adjustment()` returns adjusted values when calibration lessons exist

- [x] **Unit 5: Prediction evaluator Lambda and CDK infrastructure**

  **Goal:** Wire the evaluation logic into a scheduled Lambda with proper IAM and EventBridge scheduling.

  **Requirements:** R3

  **Dependencies:** Unit 3, Unit 4

  **Files:**
  - Create: `src/monitoring/prediction_evaluator/handler.py`
  - Modify: `infra/stacks/monitoring_stack.py`
  - Test: `tests/unit/monitoring/test_prediction_evaluator_handler.py`

  **Approach:**
  - `handler.py`: Lambda entry point `handler(event, context)` that:
    1. Instantiates `DynamoClient` for decisions, financials, companies, and lessons tables
    2. Calls `evaluate_matured_predictions()` from evaluator.py
    3. Calls `generate_lessons()` from lesson_generator.py for FALSIFIED results
    4. Returns summary dict (evaluated_count, confirmed, falsified, lessons_created)
  - CDK MonitoringStack:
    1. Add Lambda using `_fn()` factory with handler path `monitoring.prediction_evaluator.handler.handler`
    2. Grant read/write on decisions table and lessons table, read on companies and financials tables
    3. EventBridge rule: `cron(hour="7", minute="0", week_day="WED")` — Wednesday 07:00 UTC
    4. Add CloudWatch alarms via `_add_lambda_alarms()`
  - Handle GSI `ValidationException` with fallback to scan (per commit `e1a062e` pattern)

  **Patterns to follow:**
  - `infra/stacks/monitoring_stack.py` — `_fn()` factory, EventBridge scheduling, IAM policy grants
  - `src/monitoring/owners_letter/handler.py` — Lambda handler structure

  **Test scenarios:**
  - Handler invocation with no matured predictions → returns zero counts
  - Handler invocation with mixed outcomes → correct summary counts
  - DynamoDB read failure → appropriate error handling and logging
  - GSI not available → falls back to scan

  **Verification:**
  - Lambda deploys successfully via `cdk deploy`
  - EventBridge triggers on schedule
  - CloudWatch logs show evaluation results

- [x] **Unit 6: Dashboard decision journal view**

  **Goal:** Add a dashboard page showing active predictions grouped by ticker with status, deadline, and current metric value.

  **Requirements:** R6

  **Dependencies:** Unit 2

  **Files:**
  - Create: `src/dashboard/views/decision_journal.py`
  - Modify: `src/dashboard/app.py`
  - Modify: `src/dashboard/data.py`
  - Test: `tests/unit/dashboard/test_decision_journal.py`

  **Approach:**
  - `data.py`: Add `load_predictions()` function with `@st.cache_data(ttl=300)` that queries decisions table, extracts predictions from payloads, and returns a flat list of prediction dicts enriched with ticker and decision metadata
  - `decision_journal.py`: `render()` function that:
    1. Loads predictions via `data.load_predictions()`
    2. Groups by ticker with expandable sections
    3. Shows status badge (pending/confirmed/falsified), deadline, metric, threshold, actual value
    4. Summary metrics at top: total predictions, confirmation rate, upcoming deadlines
    5. Filter by status (all/pending/confirmed/falsified)
  - `app.py`: Add `"Decision Journal": "dashboard.views.decision_journal"` to `_PAGE_MODULES`

  **Patterns to follow:**
  - `src/dashboard/views/portfolio.py` — view structure, `st.cache_data`, `st.columns`, `st.metric`
  - `src/dashboard/views/pipeline.py` — table/list display pattern
  - `src/dashboard/data.py` — data loading with error handling

  **Test scenarios:**
  - No predictions exist → empty state message
  - Mix of pending/confirmed/falsified predictions → correct grouping and status display
  - Filter by status → correct filtering
  - Prediction with no actual_value yet (pending) → shows "—" for actual

  **Verification:**
  - Page renders without error in the dashboard
  - Predictions are grouped by ticker
  - Status badges reflect evaluation outcomes
  - Upcoming deadlines are visible

- [x] **Unit 7: Integrate prediction outcomes into quarterly post-mortem**

  **Goal:** Feed structured prediction outcomes into the existing quarterly post-mortem as supplementary input, enriching lesson extraction.

  **Requirements:** Success criteria: "The quarterly post-mortem receives structured prediction outcomes as input"

  **Dependencies:** Unit 3, Unit 4

  **Files:**
  - Modify: `src/monitoring/owners_letter/audit.py`
  - Modify: `src/monitoring/owners_letter/pipeline.py`
  - Test: `tests/unit/monitoring/test_owners_letter_pipeline.py`

  **Approach:**
  - In `run_outcome_audit()`, after classifying each decision, also summarize its prediction outcomes (if any): how many confirmed, falsified, pending
  - Add a `prediction_summary` field to each audit dict
  - In `generate_letter()`, include prediction accuracy stats in the prompt context so the owners' letter discusses prediction performance
  - In `extract_lessons()`, include prediction outcomes in the decision_audit input so the LLM can reference specific prediction failures when extracting deeper causal lessons

  **Patterns to follow:**
  - Existing `audit.py` audit dict structure
  - Existing `pipeline.py` prompt template replacement pattern

  **Test scenarios:**
  - Decision with 3 evaluated predictions → prediction_summary included in audit dict
  - Decision with no predictions (pre-feature) → prediction_summary is empty/null
  - Owners' letter mentions prediction accuracy stats

  **Verification:**
  - Quarterly post-mortem output references prediction outcomes
  - Lesson extraction has access to structured prediction data alongside narrative analysis

## System-Wide Impact

- **Interaction graph:** Thesis generator → allocation handler → decisions table → prediction evaluator → lessons table → lessons_client → future analysis prompts. The prediction evaluator is a new entry point (Lambda) that reads decisions and writes lessons.
- **Error propagation:** Budget exhaustion on prediction extraction should not block thesis storage. Metric lookup failure should classify as UNRESOLVABLE, not FALSIFIED. Lesson write failure should log and continue, not crash the evaluator.
- **State lifecycle risks:** Predictions are mutable state (pending → confirmed/falsified) embedded in decision items. The `update_item` call must be conditional on `status == "pending"` to prevent double-evaluation.
- **API surface parity:** The dashboard reads the same decisions table as the evaluator — no new API surface.
- **Integration coverage:** End-to-end flow from thesis generation → decision storage → weekly evaluation → lesson creation should be tested with moto mocks in an integration test.

## Risks & Dependencies

- **Two-pass LLM cost**: The Haiku extraction call adds ~$0.01-0.03 per thesis. At 50 tickers/month with ~10% passing to thesis, this is ~$0.05-0.15/month — negligible.
- **Metric availability**: Some metrics in the companies/financials tables may be null or stale. The evaluator must handle missing data gracefully (UNRESOLVABLE, not FALSIFIED).
- **Small sample noise**: The minimum sample size of 10 mitigates this, but early calibration lessons (at sample 10-15) will still be noisy. The geometric mean and [0.5, 1.5] clamp in `get_confidence_adjustment()` provides additional safety.
- **DynamoDB item size**: Adding a predictions array to decision items increases item size. With 3 predictions per item at ~500 bytes each, this adds ~1.5KB — well within DynamoDB's 400KB limit.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-03-27-decision-journaling-requirements.md](docs/brainstorms/2026-03-27-decision-journaling-requirements.md)
- Related code: `src/analysis/thesis_generator/handler.py`, `src/portfolio/allocation/handler.py`, `src/monitoring/owners_letter/audit.py`, `src/shared/lessons_client.py`
- Related pattern: `src/monitoring/owners_letter/pipeline.py` (lesson schema)
- Related pattern: `infra/stacks/monitoring_stack.py` (Lambda + EventBridge scheduling)
