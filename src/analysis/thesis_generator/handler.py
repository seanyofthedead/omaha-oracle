"""
Lambda handler for Buffett-style investment thesis generation via Claude Opus.

Only invoked for companies passing all stages: quant + moat ≥7 + mgmt ≥6 + MoS >30%.
Output: markdown stored to S3 at theses/{ticker}/{date}.md
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.config import get_config
from shared.converters import normalize_ticker, safe_float, safe_int, today_str
from shared.dynamo_client import store_analysis_result
from shared.llm_client import BudgetExhaustedError, LLMClient
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

# --- Prediction extraction constants ---

ALLOWED_METRICS = frozenset(
    {
        "revenue",
        "earnings_per_share",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "book_value_per_share",
        "debt_to_equity",
        "free_cash_flow",
        "return_on_equity",
        "stock_price",
    }
)

METRIC_TO_STAGE: dict[str, str] = {
    "revenue": "intrinsic_value",
    "earnings_per_share": "intrinsic_value",
    "free_cash_flow": "intrinsic_value",
    "stock_price": "intrinsic_value",
    "gross_margin": "moat_analysis",
    "operating_margin": "moat_analysis",
    "net_margin": "moat_analysis",
    "return_on_equity": "moat_analysis",
    "book_value_per_share": "quant_screen",
    "debt_to_equity": "quant_screen",
}

ALLOWED_OPERATORS = frozenset({">", "<", ">=", "<=", "=="})

_PREDICTION_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "prediction_extraction.md"
)


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
    mos_threshold = safe_float(event.get("mos_threshold"), default=0.30)
    if mos <= mos_threshold:
        return False, f"margin_of_safety={mos:.2%}<={mos_threshold:.0%}"

    return True, ""


def _summarise(obj: dict[str, Any] | None, max_len: int = 500) -> str:
    if not obj:
        return "No data."
    s = json.dumps(obj, indent=0, default=str)
    return s[:max_len] + "..." if len(s) > max_len else s


def _validate_prediction(pred: dict[str, Any]) -> bool:
    """Return True if a single prediction dict has all required fields and valid values."""
    metric = pred.get("metric", "")
    operator = pred.get("operator", "")
    threshold = pred.get("threshold")
    deadline = pred.get("deadline", "")
    if metric not in ALLOWED_METRICS:
        return False
    if operator not in ALLOWED_OPERATORS:
        return False
    if threshold is None:
        return False
    try:
        float(threshold)
    except (TypeError, ValueError):
        return False
    if not deadline:
        return False
    try:
        datetime.strptime(deadline, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _extract_predictions(
    client: LLMClient,
    thesis_md: str,
    ticker: str,
    company_name: str,
    sector: str,
) -> list[dict[str, Any]]:
    """Extract structured falsifiable predictions from a thesis via a cheap LLM call.

    Returns a list of 0-3 validated prediction dicts. Returns [] on any failure.
    """
    try:
        template = _PREDICTION_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _log.warning("Prediction extraction prompt not found", extra={"ticker": ticker})
        return []

    current_date = today_str()
    system_prompt = template.replace("{{ticker}}", ticker)
    system_prompt = system_prompt.replace("{{company_name}}", company_name)
    system_prompt = system_prompt.replace("{{sector}}", sector or "Unknown")
    system_prompt = system_prompt.replace("{{current_date}}", current_date)
    system_prompt = system_prompt.replace("{{thesis_markdown}}", thesis_md)

    user_prompt = (
        f"Extract 3 falsifiable quantitative predictions from the {ticker} investment thesis."
    )

    try:
        response = client.invoke(
            tier="bulk",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            module="prediction_extraction",
            ticker=ticker,
            max_tokens=1024,
            temperature=0.2,
            require_json=True,
        )
    except BudgetExhaustedError:
        _log.warning("Budget exhausted for prediction extraction", extra={"ticker": ticker})
        return []
    except Exception:
        _log.exception("Prediction extraction LLM call failed", extra={"ticker": ticker})
        return []

    content = response.get("content", {})
    if not isinstance(content, dict):
        _log.warning("Prediction extraction returned non-dict", extra={"ticker": ticker})
        return []

    raw_preds = content.get("predictions", [])
    if not isinstance(raw_preds, list):
        return []

    validated: list[dict[str, Any]] = []
    for pred in raw_preds:
        if not isinstance(pred, dict):
            continue
        if not _validate_prediction(pred):
            _log.debug(
                "Skipping invalid prediction",
                extra={"ticker": ticker, "pred": pred},
            )
            continue
        metric = pred["metric"]
        validated.append(
            {
                "description": str(pred.get("description", "")),
                "metric": metric,
                "operator": pred["operator"],
                "threshold": float(pred["threshold"]),
                "data_source": pred.get("data_source", "yahoo_finance"),
                "deadline": pred["deadline"],
                "analysis_stage": METRIC_TO_STAGE.get(metric, "intrinsic_value"),
            }
        )
        if len(validated) >= 3:
            break

    return validated


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

    # Extract structured falsifiable predictions from the thesis
    sector = event.get("sector") or event.get("company_sector") or ""
    predictions = _extract_predictions(client, thesis_md, ticker, company_name, sector)
    result["predictions"] = predictions
    if predictions:
        _log.info(
            "Predictions extracted",
            extra={"ticker": ticker, "count": len(predictions)},
        )

    store_analysis_result(
        cfg.table_analysis,
        ticker,
        "thesis_generator",
        result,
        result.get("thesis_generated", False),
    )
    return result
