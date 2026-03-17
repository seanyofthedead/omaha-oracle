"""
Lambda handler for management quality assessment via Claude Sonnet.

Evaluates owner-operator mindset, capital allocation, candor.
Budget-gated: skips LLM call if monthly budget exhausted.
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

MANAGEMENT_ASSESSMENT_PROMPT = """# Management Quality Assessment

You are evaluating the quality of management at {{company_name}} ({{ticker}}) from a Graham-Dodd-Buffett value-investing perspective.

**Context**: This company has a moat score of {{moat_score}}/10 from a prior competitive analysis. Consider how management has built or eroded that moat.

## Company Metrics

```
{{metrics}}
```

## Filing Context (from SEC EDGAR)

{{filing_context}}

---

## Your Task

Assess management quality across three dimensions:

1. **Owner-operator mindset** (1–10): Does management think and act like owners? Skin in the game, long-term orientation, frugality, focus on per-share value?
2. **Capital allocation skill** (1–10): How well does management deploy capital? Buybacks at sensible prices, M&A discipline, dividend policy, reinvestment in the business?
3. **Candor and transparency** (1–10): Does management communicate honestly? Acknowledge mistakes, avoid spin, provide useful disclosure?

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


def _get_filing_context(s3: S3Client, ticker: str) -> str:
    """Fetch recent SEC filing metadata from S3 for context."""
    prefix = f"raw/sec/{ticker}/"
    keys = s3.list_keys(prefix=prefix)
    if not keys:
        return "No SEC filing data available in S3."
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

    Input: {"ticker": "AAPL", "company_name": "...", "metrics": {...}, "moat_score": 8}
    Output: same dict with management_score and assessment fields added
    """
    cfg = get_config()
    ticker = (event.get("ticker") or "").strip().upper()
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
        _store_result(cfg, ticker, result)
        return result

    # Build prompt
    s3 = S3Client()
    filing_context = _get_filing_context(s3, ticker)
    metrics_str = _format_metrics(metrics)

    template = MANAGEMENT_ASSESSMENT_PROMPT
    system_prompt = template.replace("{{company_name}}", company_name)
    system_prompt = system_prompt.replace("{{ticker}}", ticker)
    system_prompt = system_prompt.replace("{{moat_score}}", str(moat_score))
    system_prompt = system_prompt.replace("{{metrics}}", metrics_str)
    system_prompt = system_prompt.replace("{{filing_context}}", filing_context)

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
        result["reasoning"] = f"Analysis failed: {exc}"
        result["confidence"] = 0.0
        result["skipped"] = False
        result["error"] = str(exc)
        _store_result(cfg, ticker, result)
        return result

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
    result["cost_usd"] = response.get("cost_usd")

    _store_result(cfg, ticker, result)
    return result


def _store_result(cfg: Any, ticker: str, result: dict[str, Any]) -> None:
    """Store management assessment result in analysis table."""
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    sk = f"{date_str}#management_assessment"
    analysis = DynamoClient(cfg.table_analysis)
    item = {
        "ticker": ticker,
        "analysis_date": sk,
        "screen_type": "management_assessment",
        "result": result,
        "passed": result.get("management_score", 0) >= 6,
    }
    analysis.put_item(item)
