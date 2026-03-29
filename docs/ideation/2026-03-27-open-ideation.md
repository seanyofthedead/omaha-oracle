---
date: 2026-03-27
topic: open-ideation
focus: open-ended
---

# Ideation: Omaha Oracle Open-Ended Improvement

## Codebase Context

**Project shape:** Python autonomous AI stock-picking agent using Graham-Dodd-Buffett value investing. Serverless AWS (Lambda, DynamoDB x12, S3, Step Functions, SQS, EventBridge). Streamlit dashboard. 5-stage analysis pipeline (quant screen -> moat -> management -> intrinsic value -> thesis). Self-improvement loop via quarterly post-mortem lessons.

**Notable patterns:** Externalized prompt templates with anti-style-drift guardrail. Tiered LLM routing (Opus/Sonnet/Haiku). Half-Kelly position sizing with lesson-derived confidence calibration. Hard-coded risk guardrails (15% position, 35% sector, 10% cash, zero leverage).

**Pain points:** No CI/CD pipeline. Large in-flight dashboard refactor (~20 deleted files). Quarterly feedback loop is too slow. No sell discipline equivalent to the buy pipeline. Budget exhaustion silently paralyzes the system. Pipeline stages run sequentially when some are independent.

**Past learnings:** DynamoDB GSI availability issues require fallback paths. Streamlit reruns cause toast duplication and thread safety bugs. Production code previously imported unittest.mock. HMAC auth comparison was flawed. Bare `except: pass` handlers masked real errors. Feedback loop has cold-start and overcorrection risks.

## Ranked Ideas

### 1. Decision Journaling with Pre-Committed Falsifiable Predictions
**Description:** Before each BUY, require the thesis generator to output 3 falsifiable predictions with specific time horizons and measurable thresholds (e.g., "revenue > $X by Q3 2026"). Store as structured data alongside the decision in DynamoDB. At post-mortem, automatically evaluate predictions against actuals — turning lesson extraction from "LLM guesses what went wrong" into "system knows exactly which assumption failed."
**Rationale:** Transforms the feedback loop from narrative hindsight into ground-truth signal. Every falsified prediction becomes an unambiguous learning signal that compounds lesson quality permanently. The thesis generation prompt and audit infrastructure already exist — this adds structured output, not new architecture.
**Downsides:** Adds latency to thesis generation. Requires careful prediction schema design. Some theses resist clean quantification.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Explored (brainstormed 2026-03-27)

### 2. Continuous Event-Driven Lesson Extraction
**Description:** Replace quarterly-only post-mortem with event-triggered lesson extraction. When a held position hits material thresholds (earnings miss, -20% drawdown, insider selling spike), immediately extract a micro-lesson with provisional severity. The quarterly letter becomes a summary of already-extracted lessons, not the sole extraction point.
**Rationale:** Shortens the learning loop from 90 days to days. A bad thesis pattern can repeat 10-15 times before the quarterly correction. The infrastructure supports this — `extract_lessons()` is already separable, `lessons_client` scoring is time-aware, and `expiry_quarters` already supports short-lived lessons.
**Downsides:** More frequent LLM calls for lesson extraction eat into budget. Provisional lessons need careful weighting to avoid noise. Requires new EventBridge triggers for portfolio monitoring events.
**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

### 3. Adversarial Thesis Challenge Stage (Devil's Advocate)
**Description:** Add a 6th pipeline stage after thesis generation: a separate LLM call prompted to find holes, bear cases, and cognitive biases in the generated thesis — without the anti-style-drift guardrail. The challenge score gates whether the thesis proceeds to allocation or gets downgraded. Explicitly looks for value traps, secular decline signals, and management red flags the constructive pipeline may have glossed over.
**Rationale:** The current 5-stage pipeline is purely constructive — no stage argues the other side. Confirmation bias compounds: a stock that passes quant screening gets increasingly favorable treatment at each stage. Buffett/Munger stress inversion. The guardrail that prevents style drift also prevents the system from seeing its own blind spots.
**Downsides:** Adds one Sonnet-tier LLM call per thesis (~$0.10-0.30). Could over-reject good theses if poorly calibrated. Needs a well-designed challenge prompt and scoring rubric.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 4. Sell Discipline Engine with Thesis Invalidation Monitoring
**Description:** Periodically re-evaluate the original buy thesis for each held position. Track thesis "pillars" (moat durability, management quality, margin of safety) as structured data, and trigger SELL signals when pillars break — rather than relying solely on the quarterly post-mortem to notice deterioration. Compare fresh ingestion data against thesis assumptions.
**Rationale:** The system has a sophisticated 5-stage BUY pipeline but no equivalent sell discipline. Ingestion refreshes prices regularly but results are never compared against thesis assumptions. A position can deteriorate for months between quarterly reviews with no automated detection.
**Downsides:** Continuous re-evaluation burns LLM budget. Defining "pillar broken" thresholds is non-trivial. Risk of over-selling on noise if thresholds are too tight.
**Confidence:** 80%
**Complexity:** High
**Status:** Unexplored

### 5. Shadow Portfolio for Counterfactual Learning
**Description:** Paper-track all PASS and near-miss decisions alongside the real portfolio. At post-mortem, compare actual returns against the shadow portfolio of rejected candidates to detect systematic biases in what the system avoids (e.g., consistently filtering out high-ROIC tech at high P/B ratios that then outperform).
**Rationale:** Fixes survivorship bias in the learning loop — the system only calibrates against its own picks, never from stocks it analyzed and passed on. The decisions table already stores decisions but the post-mortem only audits held positions. Omission errors are invisible without a shadow portfolio.
**Downsides:** Increases post-mortem complexity. Shadow tracking needs careful scope to avoid tracking thousands of rejections. Requires tracking shadow portfolio "returns" without actual positions.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 6. Pipeline DAG: Parallelize Moat + Management Stages
**Description:** Restructure the Step Functions state machine so moat analysis and management quality run in parallel after quant screen passes (they are independent — both depend only on quant output, not each other's results). Intrinsic value aggregates both, then thesis runs last. Cuts wall-clock pipeline time by ~40%.
**Rationale:** Moat and management are the two most expensive LLM calls (both Sonnet-tier) with zero data dependency on each other. Step Functions natively supports parallel branches. This is free latency reduction with minimal architectural change.
**Downsides:** Slightly more complex state machine definition. Error handling for one-branch-fails-while-other-succeeds needs design. CDK stack changes required.
**Confidence:** 90%
**Complexity:** Low
**Status:** Implemented (2026-03-29)

### 7. Budget Reserve for Sell-Side Analysis (Silent Paralysis Fix)
**Description:** Reserve 20% of the monthly LLM budget for sell-side analysis and emergency re-evaluation. When the general budget is exhausted, sell-path analysis can still run. Prevents the system from holding through a fraud revelation or thesis collapse because it literally ran out of budget to think.
**Rationale:** With a $50/month default budget, a large screening run early in the month can leave the system blind to deteriorating positions for weeks. The current `_assert_budget` blocks all non-thesis tiers uniformly. This is a safety-critical gap where cost control creates existential portfolio risk.
**Downsides:** Reduces effective budget for new analysis by 20%. Needs clear definition of what qualifies as "sell-side" vs general analysis. Adds complexity to budget enforcement logic.
**Confidence:** 85%
**Complexity:** Low
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Unified error boundary | Incremental; partially addressed by recent backend hardening commit |
| 2 | Dashboard session state machine | Scoped to the dashboard refactor already in progress |
| 3 | Pipeline progress streaming | High effort, low alpha impact; views/pipeline.py already being added |
| 4 | Offline-first dashboard | Over-engineered for single-user tool; poor Streamlit fit |
| 5 | CI/CD with canary validation | Standard DevOps, no novel insight; deploy.yml already exists |
| 6 | Lesson bootstrap with seed data | Artificial seeds could introduce bias |
| 7 | Cost transparency per-ticker | Low leverage; cost tracker view was intentionally removed |
| 8 | Macro regime detection | Extremely hard to calibrate; risks adding market timing to value system |
| 9 | Prompt A/B testing | Needs quarters of data for statistical significance — impractical near-term |
| 10 | Correlation-aware risk engine | High implementation burden (rolling correlations, factor models); better as v2 |
| 11 | Data triangulation across sources | Known edge case (D/E normalization), not systemic |
| 12 | Remove yfinance from audit Lambda | Narrow scope; better as a ticket |
| 13 | Prompt template schema validator | Low leverage; Jinja2 solves in an afternoon |
| 14 | CDK diff as PR check | Standard DevOps practice |
| 15 | Cached budget gate | Premature optimization at current scale |
| 16 | Dashboard refactor redirect | Tactical cleanup, not strategic |
| 17 | Self-calibrating quant screen | Dangerous — adaptive thresholds could drift from value discipline |
| 18 | Adaptive stage skipping | Undermines pipeline thoroughness guarantee |
| 19 | Dynamic model routing | Needs outcome data that doesn't exist yet |
| 20 | Portfolio-driven universe | Assumes mature portfolio; at startup the portfolio is empty |
| 21 | Cross-sector lesson generalization | Embedding infra is large dependency for uncertain payoff |
| 22 | Lesson semantic deduplication | Corpus too small to need consolidation yet |
| 23 | Quant screen caching | Premature optimization at current throughput |
| 24 | Market-halt circuit breaker | Alpaca already rejects orders on halted securities |
| 25 | DynamoDB hot-partition | On-demand handles current throughput fine |
| 26 | Tranche partial-fill zombie | Low probability with Alpaca's limit order handling |
| 27 | Adversarial prompt injection via filings | Claude is resilient; over-engineering for current risk |
| 28 | Lessons echo chamber prevention | Addressed by confidence clamping; corpus too small to echo |
| 29 | Confidence calibration wiring | One-line change — better as a task |
| 30 | Stale-price execution gap | Low probability with day-order + limit pricing |
| 31 | Adaptive LLM tier routing | Overlaps with A/B testing; needs quarters of outcome data |

## Session Log
- 2026-03-27: Initial ideation — 48 raw ideas from 6 agents, deduped to 28, 3 cross-cutting combinations synthesized, 7 survivors after adversarial filtering
- 2026-03-27: Brainstormed idea #1 (Decision Journaling) → docs/brainstorms/2026-03-27-decision-journaling-requirements.md
