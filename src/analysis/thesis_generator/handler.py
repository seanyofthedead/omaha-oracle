"""
Lambda handler for Buffett-style investment thesis generation via Claude Opus.

Only invoked for companies passing all stages: quant + moat ≥7 + mgmt ≥6 + MoS >30%.
Output: markdown stored to S3 at theses/{ticker}/{date}.md
"""

from __future__ import annotations

import json
from typing import Any

from shared.config import get_config
from shared.converters import normalize_ticker, safe_float, safe_int, today_str
from shared.dynamo_client import store_analysis_result
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

THESIS_GENERATION_PROMPT = """# Investment Thesis Generation

Write a Buffett-style investment thesis for
{{company_name}} ({{ticker}}) in plain English.
Target length: 1–2 pages. Output valid markdown only.

## Input Summary

**Quantitative screen**: {{quant_summary}}

**Moat analysis** (score {{moat_score}}/10): {{moat_summary}}

**Management assessment** (score {{management_score}}/10): {{management_summary}}

**Valuation**: {{valuation_summary}}

---

## Required Structure

Write the thesis with exactly these six sections. Use `##` for section headers.

### 1. The Business
Explain what the company does in plain English. How does it
make money? Who are its customers? What is the industry
structure?

### 2. The Moat
What are the specific competitive advantages? Be concrete. Reference the moat analysis findings.

### 3. Management
Owner-operator mindset, capital allocation, candor. What stands out positively or negatively?

### 4. Valuation
Summarise the math: DCF scenarios, EPV, asset floor,
composite intrinsic value, margin of safety. Show the
numbers.

### 5. Kill the Thesis
What could go wrong? What would trigger a sell? Be specific.

### 6. The Verdict
- Buy price (or price range)
- Position size recommendation (as % of portfolio)
- One-sentence summary

---

Output markdown only. No JSON, no preamble.
"""

MOAT_MIN = 7
MGMT_MIN = 6
MOS_MIN = 0.30


def _passes_all_stages(event: dict[str, Any]) -> tuple[bool, str]:
    """Check quant passed, moat ≥7, mgmt ≥6, MoS >30%. Returns (passed, reason)."""
    quant_passed = event.get("quant_passed", True)
    if isinstance(event.get("quant_result"), dict):
        quant_passed = event.get("quant_result", {}).get("pass", quant_passed)
    if not quant_passed:
        return False, "quant_screen"

    moat_score = safe_int(event.get("moat_score", 0))
    if moat_score < MOAT_MIN:
        return False, f"moat_score={moat_score}<{MOAT_MIN}"

    mgmt_score = safe_int(event.get("management_score", 0))
    if mgmt_score < MGMT_MIN:
        return False, f"management_score={mgmt_score}<{MGMT_MIN}"

    mos = safe_float(event.get("margin_of_safety", 0))
    if mos <= MOS_MIN:
        return False, f"margin_of_safety={mos:.2%}<={MOS_MIN:.0%}"

    return True, ""


def _summarise(obj: dict[str, Any] | None, max_len: int = 500) -> str:
    if not obj:
        return "No data."
    s = json.dumps(obj, indent=0, default=str)
    return s[:max_len] + "..." if len(s) > max_len else s


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: aggregated analysis with ticker, quant_result, moat_score,
           management_score, margin_of_safety, intrinsic_value_result, etc.
    Output: input + thesis_s3_key, thesis_generated, skipped_reason
    """
    cfg = get_config()
    ticker = normalize_ticker(event)
    company_name = (event.get("company_name") or "").strip() or ticker

    if not ticker:
        out = dict(event)
        out["thesis_generated"] = False
        out["skipped_reason"] = "ticker required"
        return out

    result = dict(event)

    passed, reason = _passes_all_stages(event)
    if not passed:
        _log.info("Skipping thesis — stages not passed", extra={"ticker": ticker, "reason": reason})
        result["thesis_generated"] = False
        result["skipped_reason"] = reason
        return result

    # Build prompt
    quant_result = event.get("quant_result") or {}
    moat_result = event.get("moat_result") or event
    mgmt_result = event.get("management_result") or event
    iv_result = event.get("intrinsic_value_result") or event

    quant_summary = _summarise(quant_result)
    moat_summary = _summarise(moat_result) if isinstance(moat_result, dict) else str(moat_result)
    management_summary = (
        _summarise(mgmt_result) if isinstance(mgmt_result, dict) else str(mgmt_result)
    )
    valuation_summary = _summarise(iv_result) if isinstance(iv_result, dict) else str(iv_result)

    moat_score = safe_int(event.get("moat_score", 0))
    mgmt_score = safe_int(event.get("management_score", 0))

    template = THESIS_GENERATION_PROMPT
    system_prompt = template.replace("{{company_name}}", company_name)
    system_prompt = system_prompt.replace("{{ticker}}", ticker)
    system_prompt = system_prompt.replace("{{quant_summary}}", quant_summary)
    system_prompt = system_prompt.replace("{{moat_score}}", str(moat_score))
    system_prompt = system_prompt.replace("{{moat_summary}}", moat_summary)
    system_prompt = system_prompt.replace("{{management_score}}", str(mgmt_score))
    system_prompt = system_prompt.replace("{{management_summary}}", management_summary)
    system_prompt = system_prompt.replace("{{valuation_summary}}", valuation_summary)

    user_prompt = f"Write the investment thesis for {company_name} ({ticker})."

    try:
        client = LLMClient()
        response = client.invoke(
            tier="thesis",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            module="thesis_generator",
            ticker=ticker,
            max_tokens=4096,
            temperature=0.3,
            require_json=False,
        )
    except Exception as exc:
        _log.exception("Thesis generation failed", extra={"ticker": ticker})
        result["thesis_generated"] = False
        result["skipped_reason"] = "error — see CloudWatch logs for details"
        result["error"] = type(exc).__name__
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "thesis_generator",
            result,
            result.get("thesis_generated", False),
        )
        raise

    thesis_md = response.get("content", "")
    if not isinstance(thesis_md, str):
        thesis_md = str(thesis_md)

    s3_key = f"theses/{ticker}/{today_str()}.md"

    s3 = S3Client()
    s3.write_markdown(s3_key, thesis_md)

    result["thesis_s3_key"] = s3_key
    result["thesis_generated"] = True
    result["skipped_reason"] = ""
    result["cost_usd"] = response.get("cost_usd")

    store_analysis_result(
        cfg.table_analysis,
        ticker,
        "thesis_generator",
        result,
        result.get("thesis_generated", False),
    )
    return result
