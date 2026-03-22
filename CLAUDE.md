# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Omaha Oracle is an autonomous AI-powered stock-picking agent built on Graham-Dodd-Buffett value investing principles. It runs serverless on AWS, collecting financial data, screening stocks quantitatively, running Claude AI analysis, calculating intrinsic values, executing trades via Alpaca, and self-improving through quarterly post-mortem reviews.

## Development Commands

```bash
# Install all dependencies (from repo root)
pip install -e ".[dev,dashboard,infra]"

# Run unit tests (no AWS credentials needed — uses moto)
pytest tests/unit/

# Run a single test file
pytest tests/unit/shared/test_cost_tracker.py -v

# Run with coverage
pytest tests/unit/ --cov=src

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy src/

# Run the Streamlit dashboard locally
python run_dashboard.py
# Opens at http://localhost:8501

# Deploy to AWS (from infra/ context)
cdk deploy --all -c env=dev
cdk deploy --all -c env=prod
cdk deploy "OmahaOracle-Dev-Data" -c env=dev   # single stack
```

## Architecture

### Layer Overview

The pipeline flows: **Ingestion → Analysis → Portfolio → Monitoring**, with a **Shared** utilities layer used by all.

```
src/
├── shared/        # Config, LLM client, DynamoDB/S3 helpers, cost tracking, lessons
├── ingestion/     # Data collection Lambdas (Yahoo Finance, SEC EDGAR, FRED, insider txns)
├── analysis/      # 5-stage pipeline: quant screen → moat → management → DCF/EPV → thesis
├── portfolio/     # Position sizing (Half-Kelly), Alpaca order execution, risk guardrails
├── monitoring/    # Quarterly owners' letter post-mortem, cost monitoring, SNS alerts
└── dashboard/     # Streamlit web UI (6 pages)
infra/stacks/      # CDK: DataStack, AnalysisStack, PortfolioStack, MonitoringStack
prompts/           # Markdown prompt templates for all Claude calls
```

### Key Shared Modules

- **`src/shared/config.py`** — Pydantic `BaseSettings` singleton (`get_config()`). Resolution order: env vars → AWS SSM Parameter Store → defaults. All 12 DynamoDB table names and the S3 bucket name are derived from `ENVIRONMENT`.
- **`src/shared/llm_client.py`** — Thin Anthropic API wrapper. Routes by tier: `"thesis"` → Opus, `"analysis"` → Sonnet, `"bulk"` → Haiku. Prepends `prompts/anti_style_drift_guardrail.md` to every prompt. Retries with exponential backoff. Budget check skipped for thesis tier only.
- **`src/shared/lessons_client.py`** — Retrieval-augmented lesson injection. Scores stored lessons by ticker/sector/stage/recency; top-5 injected into moat and management prompts.
- **`src/shared/cost_tracker.py`** — Tracks LLM spend in DynamoDB; enforces monthly budget (`MONTHLY_LLM_BUDGET_CENTS`).

### Analysis Pipeline (Step Functions)

Five chained Lambdas process each ticker, up to 3 in parallel:
1. **Quant Screen** — Pure math (Piotroski F-score, P/B, ROIC thresholds). No AI.
2. **Moat Analysis** — Claude Sonnet with injected lessons.
3. **Management Quality** — Claude Sonnet with injected lessons.
4. **Intrinsic Value** — Three valuation models: DCF, EPV, asset floor.
5. **Thesis Generation** — Claude Opus. Full Buffett-style investment thesis in Markdown.

A hard circuit breaker in the portfolio layer blocks any BUY if the quant screen failed.

### Portfolio Risk Guardrails (Hard-Coded)

Enforced in `src/portfolio/risk/guardrails.py` — these cannot be overridden by LLM output:
- Max 15% portfolio in any single position
- Max 35% sector exposure
- Min 10% cash reserve
- Zero leverage, shorts, options, derivatives, crypto

Position sizing uses Half-Kelly: `f* = (b·p − q) / (2·b)`, adjusted by lesson-derived confidence calibration factors.

### Infrastructure

Four CDK stacks in `infra/stacks/`, all resources named `omaha-oracle-{env}-*`:
- **DataStack** — S3, 12 DynamoDB tables (on-demand), SQS, Lambda layer, ingestion Lambdas + EventBridge schedules
- **AnalysisStack** — Analysis Lambdas + Step Functions state machine
- **PortfolioStack** — Allocation, execution, risk Lambdas
- **MonitoringStack** — Cost monitor, SNS alerts, owners letter Lambda

### Self-Improvement Loop

Quarterly: `monitoring/owners_letter/` Lambda runs a post-mortem audit, extracts lessons (via Claude), stores them in the `lessons` DynamoDB table. Lessons are re-injected into future analysis prompts via `lessons_client.py`. Confidence calibration thresholds are updated in the `config` DynamoDB table.

## Testing

Unit tests in `tests/unit/` use `moto` to mock all AWS services — no real credentials required. An `autouse` fixture in `conftest.py` calls `reset_config()` between tests to clear the Pydantic settings LRU cache. Integration tests in `tests/integration/` may require real AWS resources. Manual debug scripts are in `tests/manual/`.

## Environment

The `.env` file must contain: `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`, `FRED_API_KEY`, `AWS_PROFILE`, `ENVIRONMENT` (`dev` or `prod`). Paper trading is default in non-prod; live trading only activates when `ENVIRONMENT=prod`.
