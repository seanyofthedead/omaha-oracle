"""
Lambda handler for competitive moat analysis via Claude Sonnet.

Evaluates five moat sources and returns structured JSON.
Budget control: skips LLM call if monthly budget exhausted.
"""

from __future__ import annotations

import re
from typing import Any

from shared.config import get_config
from shared.converters import format_metrics, normalize_ticker
from shared.cost_tracker import CostTracker
from shared.dynamo_client import store_analysis_result
from shared.lessons_client import LessonsClient
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

MOAT_ANALYSIS_PROMPT = """# Moat Analysis

You are evaluating the competitive moat of {{company_name}}
({{ticker}}) from a Graham-Dodd-Buffett value-investing
perspective.

**Critical skepticism**: Most companies do NOT have a moat.
Be conservative. Only assign high scores when evidence is
strong and durable.

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
3. **Cost advantages** — Does the company have structural
cost advantages (scale, process, location)?
4. **Intangible assets** — Brand, patents, regulatory licenses, trade secrets?
5. **Efficient scale** — Is the market served best by one or few players (natural monopoly)?

---

## Required JSON Output

Respond with a single JSON object with exactly these keys:

- **moat_type** (string): One of "wide", "narrow", "none", or "uncertain"
- **moat_sources** (array of strings): Which of the five
sources apply, e.g. ["switching costs", "intangible assets"]
- **moat_score** (integer 1–10): Overall moat strength. 1 = no moat, 10 = exceptional
- **moat_trend** (string): "improving", "stable", "eroding", or "uncertain"
- **pricing_power** (integer 1–10): Ability to raise prices without losing customers
- **customer_captivity** (integer 1–10): How locked-in are customers?
- **reasoning** (string): 2–4 sentences explaining your assessment
- **risks_to_moat** (array of strings): Key threats that could erode the moat
- **confidence** (float 0–1): Your confidence in this assessment
"""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: {"ticker": "AAPL", "metrics": {...}, "company_name": "Apple Inc."}
    Output: {"ticker": "AAPL", "moat_score": 8, "moat_type": "...", ...}
    """
    cfg = get_config()
    ticker = normalize_ticker(event)
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
        result.update(
            {
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
            }
        )
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "moat_analysis",
            result,
            result.get("moat_score", 0) >= 6,
        )
        return result

    # Build prompt
    s3 = S3Client()
    filing_context, filing_degraded = s3.get_filing_context(ticker)
    metrics_str = format_metrics(metrics)

    template = MOAT_ANALYSIS_PROMPT
    _substitutions = {
        "company_name": company_name,
        "ticker": ticker,
        "industry": str(industry),
        "sector": str(sector),
        "metrics": metrics_str,
        "filing_context": filing_context,
    }
    system_prompt = re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: _substitutions.get(m.group(1), m.group(0)),
        template,
    )

    # Inject lessons from self-improvement feedback loop
    try:
        lessons_client = LessonsClient()
        lessons_text = lessons_client.get_relevant_lessons(
            ticker=ticker,
            sector=str(sector),
            industry=str(industry),
            analysis_stage="moat_analysis",
        )
        if lessons_text:
            system_prompt = f"{system_prompt}\n\n{lessons_text}"
    except Exception:
        _log.warning("Failed to retrieve lessons for moat analysis", extra={"ticker": ticker})

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
        result.update(
            {
                "moat_score": 0,
                "moat_type": "uncertain",
                "moat_sources": [],
                "moat_trend": "uncertain",
                "pricing_power": 0,
                "customer_captivity": 0,
                "reasoning": "Analysis failed — see CloudWatch logs for details",
                "risks_to_moat": [],
                "confidence": 0.0,
                "skipped": False,
                "error": type(exc).__name__,
            }
        )
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "moat_analysis",
            result,
            result.get("moat_score", 0) >= 6,
        )
        raise

    content = response.get("content", {})
    if not isinstance(content, dict):
        content = {}

    result = dict(result_base)
    result.update(
        {
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
            "filing_context_degraded": filing_degraded,
            "cost_usd": response.get("cost_usd"),
        }
    )

    store_analysis_result(
        cfg.table_analysis,
        ticker,
        "moat_analysis",
        result,
        result.get("moat_score", 0) >= 6,
    )
    return result
