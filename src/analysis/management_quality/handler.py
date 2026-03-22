"""
Lambda handler for management quality assessment via Claude Sonnet.

Evaluates owner-operator mindset, capital allocation, candor.
Budget-gated: skips LLM call if monthly budget exhausted.
"""

from __future__ import annotations

import re
from typing import Any

from shared.config import get_config
from shared.converters import format_metrics, normalize_ticker
from shared.cost_tracker import CostTracker
from shared.dynamo_client import store_analysis_result
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

MANAGEMENT_ASSESSMENT_PROMPT = """# Management Quality Assessment

You are evaluating the quality of management at
{{company_name}} ({{ticker}}) from a Graham-Dodd-Buffett
value-investing perspective.

**Context**: This company has a moat score of
{{moat_score}}/10 from a prior competitive analysis.
Consider how management has built or eroded that moat.

## Company Metrics

```
{{metrics}}
```

## Filing Context (from SEC EDGAR)

{{filing_context}}

---

## Your Task

Assess management quality across three dimensions:

1. **Owner-operator mindset** (1–10): Does management think
and act like owners? Skin in the game, long-term orientation,
frugality, focus on per-share value?
2. **Capital allocation skill** (1–10): How well does management
deploy capital? Buybacks at sensible prices, M&A discipline,
dividend policy, reinvestment in the business?
3. **Candor and transparency** (1–10): Does management
communicate honestly? Acknowledge mistakes, avoid spin,
provide useful disclosure?

---

## Required JSON Output

Respond with a single JSON object with exactly these keys:

- **owner_operator_mindset** (integer 1–10)
- **capital_allocation_skill** (integer 1–10)
- **candor_transparency** (integer 1–10)
- **management_score** (integer 1–10): Overall management quality
- **red_flags** (array of strings): Concerning behaviors or patterns
- **green_flags** (array of strings): Positive indicators
- **reasoning** (string): 2–4 sentences explaining your assessment
- **confidence** (float 0–1): Your confidence in this assessment
"""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: {"ticker": "AAPL", "company_name": "...", "metrics": {...}, "moat_score": 8}
    Output: same dict with management_score and assessment fields added
    """
    cfg = get_config()
    ticker = normalize_ticker(event)
    company_name = (event.get("company_name") or "").strip() or ticker
    metrics = event.get("metrics") or {}
    moat_score = event.get("moat_score", 0)

    if not ticker:
        out = dict(event)
        out["error"] = "ticker required"
        out["management_score"] = 0
        return out

    # Pass-through output: start with input
    result: dict[str, Any] = dict(event)

    # Budget check before LLM call
    tracker = CostTracker()
    status = tracker.check_budget()
    if status["exhausted"]:
        _log.warning("Budget exhausted — skipping management assessment", extra={"ticker": ticker})
        result["management_score"] = 0
        result["owner_operator_mindset"] = 0
        result["capital_allocation_skill"] = 0
        result["candor_transparency"] = 0
        result["red_flags"] = []
        result["green_flags"] = []
        result["reasoning"] = "Analysis skipped: monthly LLM budget exhausted."
        result["confidence"] = 0.0
        result["skipped"] = True
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "management_assessment",
            result,
            result.get("management_score", 0) >= 6,
        )
        return result

    # Build prompt
    s3 = S3Client()
    filing_context, filing_degraded = s3.get_filing_context(ticker)
    metrics_str = format_metrics(metrics)

    template = MANAGEMENT_ASSESSMENT_PROMPT
    _substitutions = {
        "company_name": company_name,
        "ticker": ticker,
        "moat_score": str(moat_score),
        "metrics": metrics_str,
        "filing_context": filing_context,
    }
    system_prompt = re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: _substitutions.get(m.group(1), m.group(0)),
        template,
    )

    user_prompt = f"Assess management quality at {company_name} ({ticker})."

    try:
        client = LLMClient(cost_tracker=tracker)
        response = client.invoke(
            tier="analysis",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            module="management_quality",
            ticker=ticker,
            max_tokens=2048,
            temperature=0.2,
            require_json=True,
        )
    except Exception as exc:
        _log.exception("Management assessment LLM failed", extra={"ticker": ticker})
        result["management_score"] = 0
        result["owner_operator_mindset"] = 0
        result["capital_allocation_skill"] = 0
        result["candor_transparency"] = 0
        result["red_flags"] = []
        result["green_flags"] = []
        result["reasoning"] = "Analysis failed — see CloudWatch logs for details"
        result["confidence"] = 0.0
        result["skipped"] = False
        result["error"] = type(exc).__name__
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "management_assessment",
            result,
            result.get("management_score", 0) >= 6,
        )
        raise

    content = response.get("content", {})
    if not isinstance(content, dict):
        content = {}

    result["management_score"] = content.get("management_score", 0)
    result["owner_operator_mindset"] = content.get("owner_operator_mindset", 0)
    result["capital_allocation_skill"] = content.get("capital_allocation_skill", 0)
    result["candor_transparency"] = content.get("candor_transparency", 0)
    result["red_flags"] = content.get("red_flags", [])
    result["green_flags"] = content.get("green_flags", [])
    result["reasoning"] = content.get("reasoning", "")
    result["confidence"] = content.get("confidence", 0.0)
    result["skipped"] = False
    result["filing_context_degraded"] = filing_degraded
    result["cost_usd"] = response.get("cost_usd")

    store_analysis_result(
        cfg.table_analysis,
        ticker,
        "management_assessment",
        result,
        result.get("management_score", 0) >= 6,
    )
    return result
