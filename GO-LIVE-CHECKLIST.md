# Omaha Oracle — Production Go-Live Checklist

**Last assessed:** 2026-03-21
**Branch:** `production-readiness-review`

This checklist captures every gap found during the pre-live production readiness review. Items are ordered by severity. **All BLOCKERS must be resolved before enabling live trading (`ENVIRONMENT=prod`).**

---

## Section 1 — BLOCKERS

These items represent conditions under which going live would expose real capital to unacceptable risk. Do not flip `ENVIRONMENT=prod` until every blocker is closed.

---

### 1. No CI/CD pipeline

**Files:** `.github/workflows/test.yml`, `.github/workflows/deploy.yml`

**Risk:** Both workflow files are empty. No automated tests run on push or pull request. A broken change can be merged and deployed to prod silently. Any future regression in portfolio logic, guardrails, or execution could reach live trading undetected.

**Fix:** Populate `test.yml` with `pytest tests/unit/` on every push/PR. Populate `deploy.yml` with a gated CDK deploy that only runs on merge to `main` after tests pass. Add `ruff check` and `mypy` as pre-deploy gates.

---

### 2. No idempotency on writes — risk of duplicate orders

**Files:** `src/portfolio/execution/handler.py`, `src/portfolio/allocation/handler.py`

**Risk:** SQS delivers messages at-least-once. If an execution Lambda fails mid-flight and SQS retries, the same order can be submitted to Alpaca twice. DynamoDB writes in the analysis and decisions tables have no `ConditionExpression` guard, so a retry also duplicates analysis records. Double-submitted BUY orders mean double the intended position size.

**Fix:** Add a `ConditionExpression="attribute_not_exists(pk)"` guard on all DynamoDB `put_item` calls that create new records. For the execution handler, write a dedup key (e.g., `{ticker}#{date}#{side}`) to a DynamoDB decisions table with a conditional write before submitting to Alpaca. Use SQS message deduplication IDs on FIFO queues for the execution path.

---

### 3. Dashboard has zero authentication

**File:** `src/dashboard/app.py`

**Risk:** The Streamlit dashboard is fully public. Any person with the URL can view current portfolio value, all open positions, insider transaction alerts, full investment theses, and trade signals. Exposes proprietary strategy and live financial data.

**Fix:** Add Streamlit's built-in `secrets.toml` password gate, or deploy behind AWS Cognito + ALB authentication, or restrict to a VPN/allowlist at the CloudFront/ALB layer. At minimum, add a pre-login password wall before any page renders.

---

### 4. No CloudWatch alarms on any Lambda

**Files:** `infra/stacks/analysis_stack.py`, `infra/stacks/portfolio_stack.py`, `infra/stacks/monitoring_stack.py`, `infra/stacks/data_stack.py`

**Risk:** Lambda errors, timeouts, and throttles are logged to CloudWatch but no alarms exist. A Lambda that fails silently (e.g., the allocation handler errors out before execution) leaves the operator completely unaware. Silent failures in the analysis pipeline mean stale or missing data drives the next portfolio decision.

**Fix:** Add `aws_cloudwatch.Alarm` constructs in each CDK stack for: Lambda error rate > 0, Lambda timeout rate > 0, Lambda throttle rate > 0. Route all alarms to the existing SNS topic. Add a composite alarm for the Step Functions execution failure rate.

---

### 5. Trade execution failures do not trigger SNS alerts

**File:** `src/portfolio/execution/handler.py`

**Risk:** Order failures (rejected orders, Alpaca API errors, insufficient buying power) are written to CloudWatch logs but never published to SNS. The operator has no real-time alert when a trade silently fails. A failed BUY means the intended position was never opened; a failed SELL means the portfolio stays exposed longer than intended.

**Fix:** Wrap every Alpaca order submission in a try/except that publishes a structured alert to the SNS topic on any failure. Include ticker, order side, quantity, intended price, and the error message. Mirror this to the DynamoDB `executions` table with a `FAILED` status.

---

### 6. No emergency trading kill switch

**Files:** `src/portfolio/execution/handler.py`, `src/portfolio/allocation/handler.py`

**Risk:** If a bug in allocation logic fires (e.g., runaway BUYs, wrong position sizing), there is no way to halt trading without a full CDK redeployment or manual Lambda disablement via the AWS console. During the window between bug discovery and redeployment, the system continues placing orders.

**Fix:** Add a `TRADING_ENABLED` flag to the `config` DynamoDB table. Both the allocation and execution handlers read this flag at startup and short-circuit with a log + SNS alert if `False`. Provide a one-liner runbook command to flip the flag without a redeployment:
```bash
aws dynamodb put-item --table-name omaha-oracle-prod-config \
  --item '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"},"value":{"BOOL":false}}'
```

---

### 7. Portfolio state not written back after execution — risk of double-sizing

**Files:** `src/portfolio/execution/handler.py`, `src/portfolio/allocation/handler.py`

**Risk:** After orders are submitted to Alpaca, the portfolio DynamoDB table is never updated with new positions or the post-trade cash balance. The next allocation run reads stale state and may re-recommend positions already established or size new positions against the pre-trade cash balance. Example: portfolio shows $100K cash → BUY $30K executes → portfolio still shows $100K → next run sizes another $30K BUY in the same ticker.

**Fix:** After each confirmed Alpaca order, poll Alpaca's positions endpoint as the source of truth and sync positions + cash back to the DynamoDB portfolio table. Add a confirmation step before the handler returns success.

---

### 8. Portfolio helpers hard-cap DynamoDB query at 100 rows — positions silently truncated

**File:** `src/shared/portfolio_helpers.py:40`

**Risk:** `query(limit=100)` has no `LastEvaluatedKey` continuation loop. A portfolio with more than 100 open positions silently returns only the first 100. Allocation decisions are made on an incomplete view of the portfolio, potentially over-sizing positions that already exist in the truncated tail.

**Fix:** Replace the single-page call with a paginator loop that follows `LastEvaluatedKey` until exhausted.

---

## Section 2 — HIGH PRIORITY

These items will cause incorrect behavior, data loss, or silent degradation under real trading conditions. They should be resolved in the first sprint after go-live, or before if time permits.

---

### 9. N+1 DynamoDB query pattern in allocation handler

**File:** `src/portfolio/allocation/handler.py:190-192`

**Risk:** The handler issues one DynamoDB `get_item` per ticker in the candidate list. With 20–30 tickers, this is 20–30 sequential round-trips inside a single Lambda invocation. Each call adds ~5ms of latency; under load this can exceed the Lambda timeout, causing the entire allocation run to fail silently.

**Fix:** Replace per-ticker `get_item` calls with a single `batch_get_item` request. DynamoDB batch reads up to 100 keys in one call.

---

### 10. Eight Lambda handlers have no unit tests

**Files:** `src/analysis/moat_analysis/handler.py`, `src/analysis/management_quality/handler.py`, `src/analysis/thesis_generator/handler.py`, `src/ingestion/yahoo_finance/handler.py`, `src/ingestion/fred/handler.py`, `src/ingestion/sec_edgar/handler.py`, `src/ingestion/insider_transactions/handler.py`, `src/monitoring/cost_monitor/handler.py`

**Risk:** Core logic paths — including all ingestion handlers and three of the five analysis stages — are untested. Regressions in these handlers will not be caught by CI (once CI exists). The analysis pipeline produces investment decisions; untested analysis handlers mean any future change could silently corrupt thesis output or valuation models.

**Fix:** Add moto-backed unit tests for each handler. At minimum: test the happy path (valid input → expected DynamoDB/S3 write), the error path (upstream API failure → correct error handling), and any branching logic.

---

### 11. No retry policy on PortfolioStack Lambdas

**Files:** `infra/stacks/portfolio_stack.py`, `infra/stacks/analysis_stack.py`

**Risk:** AnalysisStack Lambdas are configured with 2 retries. PortfolioStack Lambdas (allocation, execution, risk) have no retry configuration, defaulting to AWS Lambda's default of 2 async retries — but these are invoked synchronously via Step Functions and SQS, so the retries may not apply as expected. A transient failure in execution results in a missed trade with no retry.

**Fix:** Explicitly configure `retry_attempts` on all portfolio Lambdas. For the execution handler specifically, design retries to be idempotent (see Blocker #2) before enabling them.

---

### 12. Thesis tier bypasses budget check — Opus spend uncapped

**File:** `src/shared/llm_client.py:163-164`

**Risk:** The budget enforcement logic explicitly skips `BudgetExhaustedError` for the `thesis` tier. If the Step Functions state machine runs repeatedly (e.g., due to a retry bug), Opus calls accumulate without bound. A misconfigured trigger or infinite-retry loop could exhaust the monthly LLM budget in hours.

**Fix:** Apply the budget check to all tiers, including thesis. If the concern is blocking thesis generation at end-of-month, use a separate higher budget ceiling for the thesis tier rather than removing the check entirely.

---

### 13. No runbook for operational incidents

**Risk:** No documented procedures exist for: pausing trading, rolling back a bad trade, re-processing a failed ticker, recovering from Lambda failure, or resetting the DLQ. Without a runbook, the first production incident requires ad-hoc investigation under time pressure while real money is at risk.

**Fix:** Create `RUNBOOK.md` at the repo root. At minimum document: (1) how to flip the kill switch (see Blocker #6), (2) how to manually cancel an open Alpaca order, (3) how to re-run analysis for a single ticker, (4) how to drain and re-drive the DLQ, (5) how to roll back a CDK stack, (6) how to check current LLM spend.

---

### 14. No concurrency limits on ingestion Lambdas — risk of API bans

**File:** `infra/stacks/data_stack.py`

**Risk:** Ingestion Lambdas can fan out to unbounded concurrency. A large watchlist or SQS message burst could spawn dozens of simultaneous calls to SEC EDGAR or Yahoo Finance, triggering rate limiting or a temporary IP ban on those APIs, causing data gaps that propagate to analysis and portfolio decisions.

**Fix:** Set `reserved_concurrent_executions` on each ingestion Lambda. SEC EDGAR enforces 10 req/sec; Yahoo Finance has undocumented but real rate limits. A limit of 2–5 concurrent executions per ingestion Lambda is a safe starting point.

---

### 15. API keys not enforced via SSM in prod — may be visible in console

**File:** `src/shared/config.py:212-253`

**Risk:** Config resolves in order: env var → SSM → default. In production, there is no enforcement that secrets come from SSM. If `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, or `FRED_API_KEY` are set directly as Lambda environment variables, they are visible in plaintext in the AWS Lambda console to any AWS user with `lambda:GetFunctionConfiguration`.

**Fix:** In `get_anthropic_key()`, `get_alpaca_keys()`, and `get_fred_key()`, raise an error if `ENVIRONMENT=prod` and the key is sourced from a plain env var rather than SSM. Alternatively, migrate to AWS Secrets Manager with IAM-scoped access.

---

### 16. Guardrails do not account for pending orders in the same allocation batch

**File:** `src/portfolio/risk/guardrails.py:99-113`

**Risk:** Sector concentration and position limits are checked against current portfolio state only — not including other orders being evaluated in the same allocation run. Two buys in Healthcare can each pass the 35% sector check individually but collectively breach it after both execute.

**Fix:** Pass the list of pending decisions to `check_guardrails()`; sum pending + current values when evaluating position and sector limits before approving each additional order.

---

### 17. No integration tests — full pipeline exercised only in production

**Risk:** No test exercises the end-to-end flow: quant screen → moat → management → valuation → thesis → allocation → guardrails → execution. The interaction between stages is where the most dangerous regressions hide (e.g., schema mismatches, missing fields, wrong DynamoDB key structure).

**Fix:** Create `tests/integration/` with at least one test covering the full analysis pipeline and one covering the portfolio execution path, both backed by moto. These can run in CI alongside unit tests without real AWS credentials.

---

### 18. Inconsistent error logging across ingestion handlers

**Files:** `src/ingestion/yahoo_finance/handler.py`, `src/ingestion/sec_edgar/handler.py`, `src/ingestion/fred/handler.py`, `src/ingestion/insider_transactions/handler.py`

**Risk:** Each handler logs different fields on failure (some log `ticker`, some log `exc`, some log both, some neither). CloudWatch Insights queries and operational dashboards cannot reliably filter by ticker or error type across handlers. Incident response is slower as a result.

**Fix:** Standardize all ingestion handlers to log the same fields on failure: `ticker`, `stage`, `error_type`, `error_message`. Use the structured logger (`_log.exception`) with `extra={"ticker": t, "stage": "..."}` consistently.

---

## Section 3 — NICE TO HAVE

These items improve observability, security posture, or developer experience but do not block safe trading at current scale. Prioritize after blockers and high-priority items are resolved.

---

| # | Item | File(s) | Notes |
|---|------|---------|-------|
| 16 | **No AWS X-Ray tracing** | All Lambda handlers | Add `tracing=aws_lambda.Tracing.ACTIVE` in CDK; annotate cross-service calls. Enables end-to-end latency profiling of the analysis pipeline. |
| 17 | **No pre-commit hooks** | `.pre-commit-config.yaml` (missing) | `ruff`, `mypy`, and `pytest` exist but aren't enforced locally. Add a `.pre-commit-config.yaml` so dev machines catch issues before push. |
| 18 | **No backoff on Alpaca quote fetch** | `src/portfolio/execution/alpaca_client.py` | A rate-limit 429 from Alpaca causes a silent order failure. Add exponential backoff with jitter. |
| 19 | **Yahoo Finance rate limiting is implicit** | `src/ingestion/yahoo_finance/handler.py` | Relying on `yfinance` internals for rate limiting. Explicit retry logic with backoff would make failures observable. |
| 20 | **No per-Lambda IAM roles** | All `infra/stacks/` | All Lambdas in a stack share a policy set. Least-privilege isolation would limit blast radius of a compromised function. |
| 21 | **S3 uses S3-managed encryption, not KMS** | `infra/stacks/data_stack.py` | Upgrade to customer-managed KMS key for audit trail and key rotation control. |
| 22 | **No secrets rotation policy** | SSM Parameter Store | API keys (Anthropic, Alpaca, FRED) have no automated rotation. Add rotation reminders or integrate with Secrets Manager. |
| 23 | **No dry-run mode for execution Lambda** | `src/portfolio/execution/handler.py` | Paper trading covers most scenarios, but a `DRY_RUN=true` flag that logs orders without submitting would allow safer testing of execution logic changes. |
| 24 | **No dashboard tests** | `src/dashboard/` | Dashboard pages have zero automated tests. UI regressions go undetected. |
| 25 | **No Lambda layer version pinning** | `infra/stacks/data_stack.py` | Auto-update on layer change can introduce unexpected dependency changes in prod. Pin to explicit layer versions. |
| 26 | **No SBOM or vulnerability scanning** | `pyproject.toml` | Add `pip-audit` or `safety` to CI to catch known CVEs in dependencies. |
| 27 | **No cross-account deployment strategy** | `infra/` | Dev and prod in the same AWS account means a CDK mistake in dev can affect prod resources. Separate accounts are best practice. |
| 28 | **No load or performance testing** | `tests/` | No baseline for Lambda duration, DynamoDB throughput, or Step Functions execution time under realistic ticker volumes. |

---

## Checklist Summary

| Section | Count | Status |
|---------|-------|--------|
| BLOCKERS | 8 | ❌ All open |
| HIGH PRIORITY | 10 | ❌ All open |
| NICE TO HAVE | 13 | ⚠️ Deferred |

**Go-live gate:** All 8 BLOCKERS must be resolved and verified in `dev` before `ENVIRONMENT=prod` is enabled.
