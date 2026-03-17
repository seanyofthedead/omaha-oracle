"""
Lambda handler for competitive moat analysis via Claude Sonnet.

Evaluates five moat sources and returns structured JSON.
Budget control: skips LLM call if monthly budget exhausted.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.dynamo_client import DynamoClient
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

MOAT_ANALYSIS_PROMPT = """# Moat Analysis

You are evaluating the competitive moat of {{company_name}} ({{ticker}}) from a Graham-Dodd-Buffett value-investing perspective.

**Critical skepticism**: Most companies do NOT have a moat. Be conservative. Only assign high scores when evidence is strong and durable.

## Company Context

**Industry**: {{industry}}
**Sector**: {{sector}}

## Financial Metrics

```
{{metrics}}
```

## Filing Context (from SEC EDGAR)

{{filing_context}}

---

## Your Task

Evaluate {{company_name}} across **five moat sources**:

1. **Network effects** — Does the product/service become more valuable as more users adopt it?
2. **Switching costs** — How costly is it for customers to switch to a competitor?
3. **Cost advantages** — Does the company have structural cost advantages (scale, process, location)?
4. **Intangible assets** — Brand, patents, regulatory licenses, trade secrets?
5. **Efficient scale** — Is the market served best by one or few players (natural monopoly)?

---

## Required JSON Output

Respond with a single JSON object with exactly these keys:

- **moat_type** (string): One of "wide", "narrow", "none", or "uncertain"
- **moat_sources** (array of strings): Which of the five sources apply, e.g. ["switching costs", "intangible assets"]
- **moat_score** (integer 1–10): Overall moat strength. 1 = no moat, 10 = exceptional
- **moat_trend** (string): "improving", "stable", "eroding", or "uncertain"
- **pricing_power** (integer 1–10): Ability to raise prices without losing customers
- **customer_captivity** (integer 1–10): How locked-in are customers?
- **reasoning** (string): 2–4 sentences explaining your assessment
- **risks_to_moat** (array of strings): Key threats that could erode the moat
- **confidence** (float 0–1): Your confidence in this assessment
"""


def _get_filing_context(s3: S3Client, ticker: str) -> str:
    """Fetch recent SEC filing metadata from S3 for context."""
    prefix = f"raw/sec/{ticker}/"
    keys = s3.list_keys(prefix=prefix)
    if not keys:
        return "No SEC filing data available in S3."
    # Get most recent date folder (keys like raw/sec/AAPL/2026-03-15/filings.json)
    date_keys = [k for k in keys if k.endswith("/filings.json")]
    if not date_keys:
        return "No filings.json found in S3."
    latest = sorted(date_keys)[-1]
    try:
        data = s3.read_json(latest)
    except Exception as exc:
        _log.warning("Failed to read filings", extra={"key": latest, "error": str(exc)})
        return "Could not load filing metadata."
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accns = recent.get("accessionNumber", []) or []
    lines: list[str] = []
    for i in range(min(10, len(forms))):
        f = forms[i] if i < len(forms) else ""
        d = dates[i] if i < len(dates) else ""
        a = accns[i] if i < len(accns) else ""
        lines.append(f"  - {f} ({d}) {a}")
    return "Recent filings:\n" + "\n".join(lines) if lines else "No recent filings."


def _format_metrics(metrics: dict[str, Any]) -> str:
    if not metrics:
        return "No metrics provided."
    return json.dumps(metrics, indent=2, default=str)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: {"ticker": "AAPL", "metrics": {...}, "company_name": "Apple Inc."}
    Output: {"ticker": "AAPL", "moat_score": 8, "moat_type": "...", ...}
    """
    cfg = get_config()
    ticker = (event.get("ticker") or "").strip().upper()
    company_name = (event.get("company_name") or "").strip() or ticker
    metrics = event.get("metrics") or {}

    if not ticker:
        return {"error": "ticker required", "moat_score": 0}

    # Pass through all input data for downstream stages
    result_base: dict[str, Any] = dict(event)

    industry = metrics.get("industry", "Unknown")
    sector = metrics.get("sector", "Unknown")

    # Budget check before LLM call
    tracker = CostTracker()
    status = tracker.check_budget()
    if status["exhausted"]:
        _log.warning("Budget exhausted — skipping moat analysis", extra={"ticker": ticker})
        result = dict(result_base)
        result.update({
            "moat_score": 0,
            "moat_type": "none",
            "moat_sources": [],
            "moat_trend": "uncertain",
            "pricing_power": 0,
            "customer_captivity": 0,
            "reasoning": "Analysis skipped: monthly LLM budget exhausted.",
            "risks_to_moat": [],
            "confidence": 0.0,
            "skipped": True,
        })
        _store_result(cfg, ticker, result)
        return result

    # Build prompt
    s3 = S3Client()
    filing_context = _get_filing_context(s3, ticker)
    metrics_str = _format_metrics(metrics)

    template = MOAT_ANALYSIS_PROMPT
    system_prompt = template.replace("{{company_name}}", company_name)
    system_prompt = system_prompt.replace("{{ticker}}", ticker)
    system_prompt = system_prompt.replace("{{industry}}", str(industry))
    system_prompt = system_prompt.replace("{{sector}}", str(sector))
    system_prompt = system_prompt.replace("{{metrics}}", metrics_str)
    system_prompt = system_prompt.replace("{{filing_context}}", filing_context)

    user_prompt = f"Analyze the competitive moat of {company_name} ({ticker})."

    try:
        client = LLMClient(cost_tracker=tracker)
        response = client.invoke(
            tier="analysis",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            module="moat_analysis",
            ticker=ticker,
            max_tokens=2048,
            temperature=0.2,
            require_json=True,
        )
    except Exception as exc:
        _log.exception("Moat analysis LLM failed", extra={"ticker": ticker})
        result = dict(result_base)
        result.update({
            "moat_score": 0,
            "moat_type": "uncertain",
            "moat_sources": [],
            "moat_trend": "uncertain",
            "pricing_power": 0,
            "customer_captivity": 0,
            "reasoning": f"Analysis failed: {exc}",
            "risks_to_moat": [],
            "confidence": 0.0,
            "skipped": False,
            "error": str(exc),
        })
        _store_result(cfg, ticker, result)
        return result

    content = response.get("content", {})
    if not isinstance(content, dict):
        content = {}

    result = dict(result_base)
    result.update({
        "moat_score": content.get("moat_score", 0),
        "moat_type": content.get("moat_type", "uncertain"),
        "moat_sources": content.get("moat_sources", []),
        "moat_trend": content.get("moat_trend", "uncertain"),
        "pricing_power": content.get("pricing_power", 0),
        "customer_captivity": content.get("customer_captivity", 0),
        "reasoning": content.get("reasoning", ""),
        "risks_to_moat": content.get("risks_to_moat", []),
        "confidence": content.get("confidence", 0.0),
        "skipped": False,
        "cost_usd": response.get("cost_usd"),
    })

    _store_result(cfg, ticker, result)
    return result


def _store_result(cfg: Any, ticker: str, result: dict[str, Any]) -> None:
    """Store moat analysis result in analysis table."""
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    sk = f"{date_str}#moat_analysis"
    analysis = DynamoClient(cfg.table_analysis)
    item = {
        "ticker": ticker,
        "analysis_date": sk,
        "screen_type": "moat_analysis",
        "result": result,
        "passed": result.get("moat_score", 0) >= 6,
    }
    analysis.put_item(item)
