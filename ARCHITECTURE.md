# Architecture: Omaha Oracle

Omaha Oracle is an autonomous, AI-powered stock-picking agent built on Graham-Dodd-Buffett value investing principles. It runs entirely serverless on AWS: ingestion Lambdas collect financial data on schedule, a Step Functions pipeline screens and analyzes companies with Claude, portfolio Lambdas size and execute trades via Alpaca, and a quarterly self-improvement loop extracts lessons from post-mortems and injects them back into future AI prompts.

---

## End-to-End Data Flow

```
EventBridge Schedules
  ├─ daily 23:00   → Yahoo Finance Lambda  → companies table, S3 processed/prices/
  ├─ weekly Sun    → SEC EDGAR Lambda      → financials table, S3 raw/sec/
  ├─ monthly 1st   → FRED Lambda           → S3 processed/macro/
  └─ daily 23:30   → Insider Txns Lambda   → S3 raw/insider/

SQS (analysis queue)
  └─ Step Functions State Machine (up to 3 tickers in parallel)
       ├─ Stage A: Quant Screen (pure Python)
       ├─ Stage B: Moat Analysis (Claude Sonnet)
       ├─ Stage C: Management Quality (Claude Sonnet)
       ├─ Stage D: Intrinsic Value (pure Python, 3 models)
       └─ Stage E: Thesis Generator (Claude Opus)
             └─ analysis table + S3 theses/

Portfolio Allocation Lambda
  └─ check_all_guardrails() → Alpaca API → trades table + decisions table
       └─ SNS Alerts

Quarterly: EventBridge
  └─ Owners Letter Lambda
       ├─ S3 letters/
       └─ lesson_extraction → lessons table
            └─ LessonsClient injects top-5 lessons into Moat/Management prompts
                 └─ config table thresholds updated
```

---

## Architecture Layers

### Shared (`src/shared/`)
Cross-cutting utilities used by every Lambda.

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` Pydantic singleton; `get_config()` (LRU-cached). Resolution: env vars → SSM Parameter Store (`/omaha-oracle/{env}/{param}`) → defaults. Exposes `get_anthropic_key()`, `get_alpaca_keys()`, `get_fred_key()` with SSM fallback. |
| `llm_client.py` | `LLMClient.invoke(tier, user_prompt, ...)`. Routes `"thesis"` → Opus, `"analysis"` → Sonnet, `"bulk"` → Haiku. Prepends `anti_style_drift_guardrail.md` to every system prompt. Retries 3× with exponential backoff (2s/4s/8s). Raises `BudgetExhaustedError` for non-thesis tiers when budget is exceeded. |
| `lessons_client.py` | `LessonsClient.get_relevant_lessons(ticker, sector, industry, stage)` — scores lessons by ticker (+100), sector (+50), industry (+40), stage (+30), severity, and recency; returns top-5 formatted for prompt injection. `get_confidence_adjustment()` returns a float in [0.5, 1.5]. |
| `cost_tracker.py` | Logs per-call LLM spend to the `cost-tracker` DynamoDB table; enforces `monthly_llm_budget_usd`. |
| `dynamo_client.py` | Generic DynamoDB read/write/query helpers. |
| `s3_client.py` | Generic S3 JSON/Markdown put/get helpers. |
| `logger.py` | Structured JSON logging. |

### Ingestion (`src/ingestion/`)
Four independently scheduled Lambdas. No AI calls.

| Lambda | Schedule (UTC) | Data Source | Stores |
|---|---|---|---|
| `yahoo_finance` | Daily 23:00 | yfinance API | `companies` table (camelCase fields: `trailingPE`, `priceToBook`, `marketCap`, etc.), S3 `processed/prices/{ticker}/weekly_10y.json` |
| `sec_edgar` | Weekly Sun 02:00 | SEC EDGAR XBRL | `financials` table (PK: ticker, SK: period), S3 `raw/sec/{ticker}/{date}/filings.json` |
| `fred` | Monthly 1st 03:00 | FRED API (10 series: FEDFUNDS, DGS10, DGS2, T10Y2Y, CPIAUCSL, UNRATE, VIXCLS, BAMLH0A0HYM2, GDP, UMCSENT) | S3 `processed/macro/{series_id}.json` |
| `insider_transactions` | Daily 23:30 | SEC EDGAR Form 3/4/5 | S3 `raw/insider/{ticker}/{date}/form4.xml`; flags significant buys (> $100K) |

### Analysis (`src/analysis/`)
Five Lambdas chained by a Step Functions state machine. Up to 3 tickers processed concurrently.

**Stage A — Quant Screen** (no LLM)
Reads `companies` + `financials` tables; computes derived metrics and applies thresholds. Thresholds are soft-configured in the `config` DynamoDB table (defaults below):

| Metric | Default Threshold |
|---|---|
| P/E | ≤ 15.0 |
| P/B | ≤ 1.5 |
| Debt/Equity | < 0.5 |
| ROIC 10y avg | ≥ 12% |
| Positive FCF years (of 10) | ≥ 8 |
| Piotroski F-Score | ≥ 6 |

Derived metrics: Owner Earnings = NI + D&A − 0.7×Capex; EPV = Owner Earnings / 0.10; Graham Number = √(22.5 × EPS × BookValue); ROIC = NI / (Equity + LTD).

**Stage B — Moat Analysis** (Claude Sonnet + lesson injection)
Evaluates network effects, switching costs, cost advantages, intangible assets, efficient scale.
Output: `moat_score` (1–10), `moat_type`, `moat_sources`, `moat_trend`, `pricing_power`, `customer_captivity`.
**Gate:** moat_score ≥ 7 to continue.

**Stage C — Management Quality** (Claude Sonnet + lesson injection)
Evaluates owner-operator mindset, capital allocation skill, candor/transparency.
Output: `management_score` (1–10), `owner_operator_mindset`, `capital_allocation_skill`, `red_flags`, `green_flags`.

**Stage D — Intrinsic Value** (pure Python)
Three valuation models: DCF (10-year projection), EPV (owner earnings / WACC), asset floor (Graham Number / net-net).
Output: `intrinsic_value`, `intrinsic_value_per_share`, `margin_of_safety`.
**Gate:** margin_of_safety > 30% to continue.

**Stage E — Thesis Generator** (Claude Opus)
Generates a full Buffett-style investment thesis in Markdown.
Output: `thesis_text`, `recommended_action` (BUY/HOLD/SELL), key catalysts, exit triggers.
Stored in `analysis` table and S3 `theses/{ticker}/{date}.json`.

### Portfolio (`src/portfolio/`)

**Allocation** (`position_sizer.py`) — `calculate_position_size()` implements Half-Kelly: `f* = (b·p − q) / (2·b)`. Result clamped to [min $2,000, 15% of portfolio value].

**Risk** (`guardrails.py`) — Hard limits enforced before any order:
- `check_all_guardrails(proposed_action, portfolio_state, budget_status)` → `{"passed": bool, "violations": list}`
- `validate_analysis_consistency(llm_signal, quant_screen_passed, moat_score, margin_of_safety)` — circuit breaker: rejects BUY if quant screen failed, moat < 7, or MoS ≤ 30%
- Absolute limits: max 15% per position, max 35% per sector, min 10% cash, equity-only (no shorts/options/leverage/crypto)

**Execution** (`execution/`) — Alpaca API order placement. Paper trading unless `ENVIRONMENT=prod`.

### Monitoring (`src/monitoring/`)

| Lambda | Trigger | Purpose |
|---|---|---|
| `owners_letter` | Quarterly EventBridge | Generates reflective post-mortem letter (Claude); extracts structured lessons; updates thresholds in `config` table; saves to S3 `letters/` |
| `cost_monitor` | Scheduled | Reads `cost-tracker` table; publishes SNS alert if budget threshold crossed |
| `alerts` | SNS topic | Routes alerts to configured email |

### Dashboard (`src/dashboard/`)
Streamlit app (`run_dashboard.py`). Six pages:
1. **Portfolio Overview** — positions, cost basis, market value, gain/loss
2. **Watchlist** — moat/management scores, intrinsic value, margin of safety
3. **Signals** — recent BUY/SELL decisions with reasoning
4. **Cost Tracker** — monthly budget vs. spent, utilization chart
5. **Owner's Letters** — archived quarterly letters from S3, rendered as Markdown
6. **Feedback Loop** — active lessons, confidence calibration by stage, screening thresholds, mistake-rate trend

---

## All Entry Points

| Trigger | Schedule / Event | Handler | Purpose |
|---|---|---|---|
| EventBridge | Daily 23:00 UTC | `ingestion.yahoo_finance.handler` | Fetch prices, fundamentals |
| EventBridge | Weekly Sun 02:00 UTC | `ingestion.sec_edgar.handler` | Fetch 10-year XBRL financials |
| EventBridge | Monthly 1st 03:00 UTC | `ingestion.fred.handler` | Fetch macro indicators |
| EventBridge | Daily 23:30 UTC | `ingestion.insider_transactions.handler` | Fetch insider filings |
| SQS message | On demand | Step Functions state machine | Full analysis pipeline for a ticker |
| EventBridge | Quarterly | `monitoring.owners_letter.handler` | Post-mortem + lesson extraction |
| EventBridge | Scheduled | `monitoring.cost_monitor.handler` | Budget monitoring |
| `python run_dashboard.py` | Manual | `dashboard/app.py` | Local Streamlit UI |
| `cdk deploy` | Manual | `infra/app.py` | Deploy/update all cloud infrastructure |

---

## Data Stores

### DynamoDB Tables (9 tables, on-demand billing, PITR enabled)

All named `omaha-oracle-{env}-{name}`:

| Table | PK | SK | Contents |
|---|---|---|---|
| `companies` | ticker | — | yfinance fundamentals (camelCase) |
| `financials` | ticker | period | 10-year SEC XBRL financials |
| `analysis` | ticker | analysis_date | All pipeline stage outputs |
| `portfolio` | pk (`ACCOUNT`/`POSITION`) | sk (`SUMMARY`/ticker) | Account + positions |
| `cost-tracker` | month_key (`YYYY-MM`) | timestamp | Per-call LLM cost log |
| `config` | config_key | — | Screening thresholds, feature flags |
| `decisions` | decision_id | timestamp | Buy/sell signals with full reasoning |
| `watchlist` | ticker | — | Tickers under active analysis |
| `lessons` | lesson_type | lesson_id | Structured lessons from post-mortems |

### S3 Bucket (`omaha-oracle-{env}-data`)
Versioned, server-side encrypted. Lifecycle: `raw/` → Infrequent Access after 90 days.

```
raw/
  sec/{ticker}/{date}/filings.json
  insider/{ticker}/{date}/form4.xml
processed/
  prices/{ticker}/weekly_10y.json
  macro/{series_id}.json
theses/
  {ticker}/{date}.json
letters/
  {quarter_YYYY_Q}.md
```

---

## Prompt System (`prompts/`)

| File | Used By | Purpose |
|---|---|---|
| `anti_style_drift_guardrail.md` | All LLM calls | Prepended to every system prompt; prohibits momentum trading, technical analysis, market timing, shorts, options, leverage, crypto |
| `system_prompt_base.md` | Base scaffold | Foundation for all Claude calls |
| `moat_analysis.md` | Stage B | Moat evaluation instructions; requires JSON output |
| `management_assessment.md` | Stage C | Management quality instructions; requires JSON output |
| `thesis_generation.md` | Stage E | Full investment thesis instructions; requires JSON output |
| `owners_letter.md` | Owners Letter Lambda | Quarterly post-mortem template |
| `lesson_extraction.md` | Owners Letter Lambda | Structured lesson extraction template |

---

## Self-Improvement Loop

```
Quarterly Owners Letter Lambda
  → Claude Sonnet generates reflective post-mortem
  → Claude extracts structured lessons (lesson_extraction.md prompt)
  → Lessons stored in DynamoDB `lessons` table with:
       lesson_type: moat_bias | management_bias | valuation_bias | threshold_adjustment
       severity: critical | high | moderate | minor
       expires_at: (TTL, ~8 quarters)
  → LessonsClient.get_relevant_lessons() scores and injects top-5 into Stage B/C prompts
  → Confidence calibration: get_confidence_adjustment() → position sizing adjustment [0.5, 1.5]
  → Threshold adjustments written back to `config` DynamoDB table
```

---

## Infrastructure (`infra/`)

Four CDK stacks, all resources named `omaha-oracle-{env}-*`:

| Stack | Key Resources |
|---|---|
| `DataStack` | S3 bucket, 9 DynamoDB tables, SQS analysis queue, Lambda layer (shared deps), 4 ingestion Lambdas + EventBridge rules |
| `AnalysisStack` | 5 analysis Lambdas, Step Functions state machine (Map state, max concurrency 3) |
| `PortfolioStack` | Allocation Lambda, execution Lambda, risk Lambda; IAM for DynamoDB + Alpaca |
| `MonitoringStack` | Cost monitor Lambda, SNS topic, owners letter Lambda |

CDK entrypoint: `infra/app.py`. Context key `env` (`dev`/`prod`) controls all resource naming and behavior. `ENVIRONMENT=prod` required to enable live trading.

---

## Security Posture

### Lambda / API layer

There is no public HTTP API. All Lambdas are triggered by SQS messages, EventBridge rules, or Step Functions — never by API Gateway or HTTP. As a result:

- **CORS** — not applicable. Lambda never issues HTTP responses with `Access-Control-*` headers.
- **CSP headers** — not applicable. Lambda returns JSON dicts to the invoker, not HTTP responses.
- **Cookies / Set-Cookie** — not applicable. Authentication is via IAM roles and boto3; no session tokens are issued.

### S3

The data bucket enforces `BLOCK_ALL` public access in CDK (`DataStack`). No bucket policy or ACL grants public read/write.

### Streamlit dashboard

The dashboard is a local development tool (`http://localhost:8501`). Security-relevant settings are pinned in `.streamlit/config.toml`:

| Setting | Value | Effect |
|---|---|---|
| `server.enableCORS` | `true` | Restricts WebSocket/HTTP connections to same origin |
| `server.enableXsrfProtection` | `true` | Sets `_xsrf` cookie with `SameSite=Strict` to prevent CSRF |
| `server.headless` | `true` | Suppresses Streamlit version disclosure in response headers |

Streamlit does not support injecting arbitrary `Content-Security-Policy` headers without a reverse proxy. This is acceptable for a local dev tool; if the dashboard is ever deployed publicly, add a reverse proxy (Nginx or CloudFront) with CSP and HSTS headers.
