---
date: 2026-03-27
topic: decision-journaling
---

# Decision Journaling with Falsifiable Predictions

## Problem Frame

The self-improvement feedback loop relies on retrospective narrative: at quarterly post-mortem, the LLM guesses what went wrong with bad decisions. This produces low-signal, potentially biased lessons. The thesis prompt already generates a "Kill the Thesis" section with failure scenarios, but this output is unstructured markdown stored in S3 — never machine-evaluated.

The system needs pre-committed, measurable predictions attached to every BUY decision so that learning happens from ground-truth signal (prediction vs actual) rather than narrative hindsight.

## Requirements

- R1. **Structured predictions on every BUY thesis.** The thesis generator must output exactly 3 falsifiable predictions alongside the thesis markdown. Each prediction has: a plain-English description, a measurable metric name, a threshold value, a comparison operator (>, <, =), a data source (e.g., Yahoo Finance revenue, SEC 10-K gross margin), and a deadline (ISO date).
- R2. **Predictions stored as structured data.** Predictions are stored in the decisions table payload as a `predictions` array, co-located with the existing decision record. Each prediction gets a unique ID (`pred_{decision_id}_{index}`).
- R3. **Automated weekly evaluation.** A scheduled check (weekly) scans for predictions whose deadline has passed. For each matured prediction, fetch the actual metric value from the relevant data source and compare against the threshold. Classify as CONFIRMED or FALSIFIED.
- R4. **Auto-extracted micro-lessons from falsified predictions.** Each FALSIFIED prediction immediately generates a structured lesson without an LLM call. The lesson includes: the prediction text, expected vs actual values, the ticker, sector, and the analysis stage that produced the underlying assumption (mapped from the metric type — e.g., revenue predictions map to intrinsic_value stage, moat-related predictions map to moat_analysis stage). Lesson severity derived from magnitude of miss.
- R5. **Prediction accuracy feeds into confidence calibration.** Track prediction accuracy rates per (analysis_stage, sector) pair. Feed aggregate accuracy into the existing `confidence_calibration` mechanism in lessons_client. Stages/sectors with lower prediction accuracy get lower confidence adjustment factors, which reduces position sizes via Half-Kelly.
- R6. **Prediction tracking visible in dashboard.** The dashboard displays active predictions grouped by ticker, with status (pending/confirmed/falsified), deadline, and current metric value where available.

## Success Criteria

- Every new BUY decision has exactly 3 structured, machine-evaluable predictions stored in the decisions table
- Matured predictions are auto-evaluated weekly with no LLM cost
- Falsified predictions produce lessons within one weekly evaluation cycle (not quarterly)
- After 2+ quarters, prediction accuracy metrics by stage/sector are available and flowing into confidence calibration
- The quarterly post-mortem receives structured prediction outcomes as input (replacing narrative guessing for covered decisions)

## Scope Boundaries

- **BUY decisions only.** SELL, HOLD, and NO_BUY decisions do not get predictions in v1.
- **No LLM call for evaluation.** Predictions must be evaluable by comparing a number to a threshold — no qualitative assessment.
- **No new data sources.** Predictions must reference metrics available from existing ingestion (Yahoo Finance, SEC EDGAR, FRED). If a metric isn't available, the thesis generator should not predict it.
- **No changes to the thesis markdown format.** The "Kill the Thesis" section continues to exist as prose. Predictions are a separate structured output, not a replacement.
- **Prediction evaluation does not trigger trades.** Falsified predictions create lessons and update calibration, but do not directly trigger SELL orders. (Sell discipline is a separate ideation item.)

## Key Decisions

- **Fully quantitative predictions**: Each prediction must have a numeric threshold and comparison operator. No qualitative scenarios. This maximizes auto-evaluation and eliminates LLM cost at evaluation time.
- **Per-prediction scheduling, not quarterly batch**: Each prediction has its own deadline. A weekly sweep evaluates matured predictions immediately. This breaks the 90-day feedback bottleneck.
- **Auto-extracted micro-lessons (no LLM)**: Failed predictions generate lessons mechanically — the prediction already contains expected vs actual. No LLM call needed. This is both cheaper and higher signal than narrative extraction.
- **Stage+sector granular calibration**: Prediction accuracy feeds into the existing confidence_calibration system at (analysis_stage, sector) granularity, not just globally. This lets the system learn "we're bad at predicting revenue for tech companies" separately from "we're good at predicting margins for consumer staples."
- **Reframe from "add predictions" to "structure Kill the Thesis"**: The thesis prompt already generates failure scenarios. The key change is requiring 3 of them to be machine-evaluable, not inventing a new concept.

## Dependencies / Assumptions

- Thesis generator currently outputs raw markdown (`require_json=False`). Adding structured predictions requires changing to a two-part output (markdown thesis + JSON predictions) or a separate follow-up extraction call.
- Yahoo Finance and SEC EDGAR ingestion must provide the metrics referenced in predictions. The thesis generator prompt must be constrained to only predict metrics the system can actually look up.
- The existing `confidence_calibration` mechanism in lessons_client supports per-stage, per-sector adjustment factors — this is confirmed in the codebase.

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] How should the thesis generator output structured predictions alongside markdown? Options: two-pass (markdown then JSON extraction), single structured response with markdown field, or append JSON block to markdown.
- [Affects R1][Needs research] What is the canonical set of predictable metrics available from existing ingestion sources? The prompt must constrain predictions to this set.
- [Affects R3][Technical] Should the weekly evaluator be a new Lambda or an extension of an existing monitoring Lambda?
- [Affects R4][Technical] How should auto-generated lessons map prediction metric types to analysis stages? Need a mapping table (revenue/earnings → intrinsic_value, market_share/brand → moat_analysis, etc.).
- [Affects R5][Technical] What minimum sample size is needed before prediction accuracy should influence confidence calibration? (Avoid noisy calibration from small samples.)

## Next Steps
→ `/ce:plan` for structured implementation planning
