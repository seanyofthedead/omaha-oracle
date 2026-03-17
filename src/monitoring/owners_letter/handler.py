"""
Quarterly post-mortem engine — 5 phases.

Phase 1: Outcome audit (decisions vs prices)
Phase 2: Letter generation (LLM, S3)
Phase 3: Lesson extraction (LLM, DynamoDB)
Phase 4: Threshold adjustment (config table)
Phase 5: Downstream — analysis handlers inject lessons (LessonsClient)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import yfinance as yf
from boto3.dynamodb.conditions import Attr, Key

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.lessons_client import LessonsClient
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

OWNERS_LETTER_PROMPT = """# Owner's Letter — Quarterly Post-Mortem

You are writing a brutally honest quarterly letter to yourself, as a value investor holding yourself accountable. Channel Warren Buffett's candor in his annual letters: no excuses, no spin, only truth.

## Required Tone

- **Brutal honesty**: Acknowledge every mistake. Do not rationalize.
- **Specificity**: Name tickers, cite numbers, quote your reasoning.
- **Self-criticism**: What did YOU get wrong? Not "the market" — you.

## Required Sections (in order)

### 1. Opening
2-3 sentences: quarter summary, overall performance vs. S&P, one-line verdict.

### 2. Portfolio Review
- Current positions with cost basis, current value, unrealized P&L
- Cash balance, total portfolio value
- Sector allocation breakdown

### 3. Decision Audit
For **every** decision in the quarter, list:
- **Ticker** | **Signal** (BUY/SELL/NO_BUY) | **Date** | **Price at decision** | **Current price** | **Outcome** (e.g. GOOD_BUY, BAD_BUY, MISSED_OPPORTUNITY)
- One sentence on what you were thinking at decision time

### 4. Mistakes & Root Causes
Categorize mistakes by type:
- **BAD_BUY**: What went wrong? (moat overestimated? valuation wrong? management?)
- **BAD_SELL**: Why did you sell? Was the thesis actually broken?
- **MISSED_OPPORTUNITY**: Why did you pass? What threshold or bias blocked you?

### 5. Lessons Learned
3-5 specific, actionable lessons. Each must be:
- Concrete (not "be more careful")
- Tied to a specific decision or pattern
- Usable in future analysis (e.g. "Reduce moat confidence for companies with >60% revenue from single customer")

### 6. Market Environment
1-2 paragraphs: What happened in the market this quarter? How did it affect your holdings?

### 7. Self-Improvement Plan
2-3 specific changes you will make next quarter. Tie each to a lesson above.

---

## Input Data

**Quarter**: {{quarter}}
**Audit Summary**: {{audit_summary}}

**Decision Outcomes** (every decision with classification):
```
{{decision_audit}}
```

**Portfolio Summary**:
```
{{portfolio_summary}}
```

**Previous Active Lessons** (for continuity — do not repeat these mistakes):
```
{{previous_lessons}}
```

---

Write the full letter in markdown. No placeholders. Every section must be completed.
"""

LESSON_EXTRACTION_PROMPT = """# Lesson Extraction — Structured Output

You are a quantitative analyst extracting structured lessons from a quarterly Owner's Letter and decision audit.

## Task

Extract **one lesson per BAD_BUY, BAD_SELL, and MISSED_OPPORTUNITY** from the letter. For GOOD decisions, extract a lesson ONLY if the reasoning was flawed (lucky outcome).

## Lesson Schema (each lesson MUST have)

- **lesson_id** (string): Format "Q{N}_{year}_{index}", e.g. "Q1_2026_1"
- **lesson_type** (string): One of moat_bias | valuation_bias | management_bias | sector_bias | threshold_adjustment | process_improvement | data_quality
- **severity** (string): minor | moderate | high | critical
- **description** (string): 1-2 sentences describing what went wrong
- **actionable_rule** (string): Specific rule for future analysis, e.g. "When revenue concentration >60%, reduce moat score by 2"
- **prompt_injection_text** (string): 2-3 sentences for future prompts — what should analysts see when evaluating similar situations
- **ticker** (string, optional): Ticker if lesson is company-specific, else ""
- **sector** (string, optional): Sector if lesson is sector-specific, else "ALL"
- **expiry_quarters** (integer): 4-12, how many quarters this lesson stays active before review

## Optional (include when applicable)

- **threshold_adjustment** (object): { "parameter": string, "proposed_value": number, "scope": string }
- **confidence_calibration** (object): { "analysis_stage": string, "bias_direction": "over"|"under", "adjustment_factor": number 0.7-1.3 }

## Rules

- Be specific — "be more careful" is NOT a lesson
- prompt_injection_text must be 2-3 complete sentences, usable verbatim in analysis prompts
- For BAD_BUY: lesson_type is usually moat_bias, valuation_bias, or management_bias
- For MISSED_OPPORTUNITY: often threshold_adjustment or sector_bias
- adjustment_factor: <1.0 if we overestimated (reduce confidence), >1.0 if we underestimated

## Input

**Letter**:
```
{{letter_text}}
```

**Decision Audit (with outcomes)**:
```
{{audit_json}}
```

---

Respond with a single JSON object:
```json
{
  "lessons": [
    {
      "lesson_id": "Q1_2026_1",
      "lesson_type": "moat_bias",
      "severity": "moderate",
      "description": "...",
      "actionable_rule": "...",
      "prompt_injection_text": "...",
      "ticker": "AAPL",
      "sector": "Technology",
      "expiry_quarters": 6,
      "threshold_adjustment": null,
      "confidence_calibration": { "analysis_stage": "moat_analysis", "bias_direction": "over", "adjustment_factor": 0.85 }
    }
  ]
}
```
"""

# Outcome classification thresholds
GOOD_BUY_GAIN_PCT = 0.15
BAD_BUY_LOSS_PCT = 0.20
GOOD_SELL_DROP_PCT = 0.10
BAD_SELL_RISE_PCT = 0.20
MISSED_OPP_RISE_PCT = 0.30

# Threshold adjustment cap
MAX_CHANGE_PCT_PER_QUARTER = 0.20


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the quarter."""
    month_start = (quarter - 1) * 3 + 1
    start = datetime(year, month_start, 1, tzinfo=UTC)
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(seconds=1)
    else:
        end = datetime(year, month_start + 3, 1, tzinfo=UTC) - timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _fetch_price(ticker: str, date: datetime | None = None) -> float:
    """Fetch price for ticker at date (or current if date is None)."""
    try:
        t = yf.Ticker(ticker)
        if date:
            end = date + timedelta(days=1)
            df = t.history(start=date.date(), end=end.date())
            if df.empty:
                info = t.info or {}
                return _safe_float(info.get("previousClose") or info.get("regularMarketPrice"))
            return float(df["Close"].iloc[-1])
        info = t.info or {}
        return _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    except Exception as exc:
        _log.warning("Price fetch failed", extra={"ticker": ticker, "error": str(exc)})
        return 0.0


def _classify_outcome(
    signal: str,
    price_at_decision: float,
    current_price: float,
) -> str:
    """Classify decision outcome."""
    if price_at_decision <= 0:
        return "UNKNOWN"
    if signal == "BUY":
        ret = (current_price - price_at_decision) / price_at_decision
        if ret >= GOOD_BUY_GAIN_PCT:
            return "GOOD_BUY"
        if ret <= -BAD_BUY_LOSS_PCT:
            return "BAD_BUY"
        return "NEUTRAL_BUY"
    if signal == "SELL":
        ret = (current_price - price_at_decision) / price_at_decision
        if ret <= -GOOD_SELL_DROP_PCT:
            return "GOOD_SELL"
        if ret >= BAD_SELL_RISE_PCT:
            return "BAD_SELL"
        return "NEUTRAL_SELL"
    if signal == "NO_BUY":
        if current_price > 0 and price_at_decision > 0:
            ret = (current_price - price_at_decision) / price_at_decision
            if ret >= MISSED_OPP_RISE_PCT:
                return "MISSED_OPPORTUNITY"
        return "CORRECT_PASS"
    return "UNKNOWN"


def _run_outcome_audit(
    decisions_client: DynamoClient,
    year: int,
    quarter: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Phase 1: Scan decisions, compare prices, classify outcomes."""
    start_iso, end_iso = _quarter_bounds(year, quarter)
    items = decisions_client.scan_all(
        filter_expression=Attr("timestamp").between(start_iso, end_iso),
    )

    audits: list[dict[str, Any]] = []
    sector_mistakes: dict[str, int] = {}
    bad_buy_moat_scores: list[float] = []
    mistake_count = 0

    for item in items:
        signal = (item.get("signal") or "").upper()
        decision_type = (item.get("decision_type") or "").upper()
        ticker = (item.get("ticker") or "").strip().upper()
        payload = item.get("payload") or {}
        if not ticker:
            ticker = (payload.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        ts_str = item.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = datetime.now(UTC)

        price_at = _safe_float(
            payload.get("price_at_decision")
            or payload.get("limit_price")
            or payload.get("current_price")
        )
        if price_at <= 0:
            price_at = _fetch_price(ticker, ts)

        current_price = _fetch_price(ticker, None)

        if signal not in ("BUY", "SELL"):
            if decision_type == "ORDER":
                signal = "BUY" if (item.get("side") or "").lower() == "buy" else "SELL"
            elif decision_type == "BUY":
                signal = "NO_BUY" if payload.get("signal") == "NO_BUY" else "BUY"
            elif decision_type == "SELL":
                signal = "SELL" if payload.get("signal") == "SELL" else "HOLD"

        outcome = _classify_outcome(signal, price_at, current_price)
        if outcome in ("BAD_BUY", "BAD_SELL", "MISSED_OPPORTUNITY"):
            mistake_count += 1
            sector_mistakes["Unknown"] = sector_mistakes.get("Unknown", 0) + 1
            if outcome == "BAD_BUY":
                bad_buy_moat_scores.append(_safe_float(payload.get("moat_score")))

        audits.append({
            "decision_id": item.get("decision_id"),
            "ticker": ticker,
            "signal": signal,
            "timestamp": ts_str,
            "price_at_decision": price_at,
            "current_price": current_price,
            "outcome": outcome,
            "payload": payload,
        })

    summary = {
        "total_decisions": len(audits),
        "mistake_rate": mistake_count / max(len(audits), 1),
        "sector_mistakes": sector_mistakes,
        "avg_moat_score_on_bad_buys": (
            sum(bad_buy_moat_scores) / len(bad_buy_moat_scores)
            if bad_buy_moat_scores
            else 0
        ),
    }
    return audits, summary


def _load_portfolio_summary(portfolio_client: DynamoClient) -> dict[str, Any]:
    """Load portfolio summary for letter."""
    account = portfolio_client.get_item({"pk": "ACCOUNT", "sk": "SUMMARY"})
    positions = portfolio_client.query(Key("pk").eq("POSITION"), limit=100)
    return {
        "cash": _safe_float(account.get("cash_available", 0)) if account else 0,
        "portfolio_value": _safe_float(account.get("portfolio_value", 0)) if account else 0,
        "positions": [
            {
                "ticker": p.get("sk") or p.get("ticker"),
                "shares": _safe_float(p.get("shares", 0)),
                "cost_basis": _safe_float(p.get("cost_basis", 0)),
                "market_value": _safe_float(p.get("market_value", 0)),
            }
            for p in positions
        ],
    }


def _load_previous_lessons(lessons_client: LessonsClient) -> str:
    """Format active lessons for letter context."""
    from shared.lessons_client import HEADER

    all_lessons: list[dict[str, Any]] = []
    for lt in ["moat_bias", "valuation_bias", "management_bias", "sector_bias"]:
        items = lessons_client._db.query(
            Key("lesson_type").eq(lt),
            filter_expression=Attr("active").eq(True),
        )
        all_lessons.extend(items)
    if not all_lessons:
        return "No previous lessons on record."
    lines = [HEADER, ""]
    for lesson in all_lessons[:15]:
        text = lesson.get("prompt_injection_text") or lesson.get("description", "")
        lines.append(f"- [{lesson.get('quarter', '?')}]: {text}")
    return "\n".join(lines)


def _generate_letter(
    llm_client: LLMClient,
    s3_client: S3Client,
    quarter: str,
    audit_summary: dict[str, Any],
    decision_audit: list[dict[str, Any]],
    portfolio_summary: dict[str, Any],
    previous_lessons: str,
    year: int,
    q_num: int,
) -> tuple[str, str]:
    """Phase 2: Generate letter via LLM, store to S3."""
    template_path = PROMPTS_DIR / "owners_letter.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else ""

    audit_text = json.dumps(decision_audit, indent=2, default=str)
    portfolio_text = json.dumps(portfolio_summary, indent=2, default=str)

    system_prompt = template.replace("{{quarter}}", quarter)
    system_prompt = system_prompt.replace("{{audit_summary}}", json.dumps(audit_summary, indent=2))
    system_prompt = system_prompt.replace("{{decision_audit}}", audit_text)
    system_prompt = system_prompt.replace("{{portfolio_summary}}", portfolio_text)
    system_prompt = system_prompt.replace("{{previous_lessons}}", previous_lessons)

    user_prompt = f"Write the full Owner's Letter for {quarter}. Be brutally honest."

    response = llm_client.invoke(
        tier="analysis",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        module="owners_letter",
        max_tokens=4096,
        temperature=0.3,
        require_json=False,
    )
    letter_md = response.get("content", "")
    if not isinstance(letter_md, str):
        letter_md = str(letter_md)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    letter_key = f"letters/{year}/Q{q_num}_{date_str}.md"
    s3_client.write_markdown(letter_key, letter_md)
    return letter_key, letter_md


def _extract_lessons(
    llm_client: LLMClient,
    lessons_client: LessonsClient,
    letter_md: str,
    decision_audit: list[dict[str, Any]],
    year: int,
    quarter: int,
) -> list[dict[str, Any]]:
    """Phase 3: Extract structured lessons via LLM, store to DynamoDB."""
    template = LESSON_EXTRACTION_PROMPT

    system_prompt = template.replace("{{letter_text}}", letter_md)
    system_prompt = system_prompt.replace(
        "{{audit_json}}",
        json.dumps(decision_audit, indent=2, default=str),
    )

    user_prompt = f"Extract structured lessons from the Q{quarter} {year} Owner's Letter."

    response = llm_client.invoke(
        tier="analysis",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        module="lesson_extraction",
        max_tokens=4096,
        temperature=0.2,
        require_json=True,
    )
    content = response.get("content", {})
    if not isinstance(content, dict):
        content = {}
    raw_lessons = content.get("lessons", [])
    if not isinstance(raw_lessons, list):
        raw_lessons = []

    q_label = f"Q{quarter}_{year}"
    now = datetime.now(UTC)
    created = now.isoformat()
    extracted: list[dict[str, Any]] = []

    for i, lesson in enumerate(raw_lessons):
        if not isinstance(lesson, dict):
            continue
        lesson_id = lesson.get("lesson_id") or f"{q_label}_{i+1}"
        lesson_type = (lesson.get("lesson_type") or "process_improvement").lower()
        expiry_q = int(lesson.get("expiry_quarters", 8))
        expiry_q = max(4, min(12, expiry_q))
        expiry_date = now + timedelta(days=expiry_q * 91)
        expires_at = expiry_date.isoformat()

        item = {
            "lesson_type": lesson_type,
            "lesson_id": lesson_id,
            "severity": (lesson.get("severity") or "moderate").lower(),
            "description": lesson.get("description", ""),
            "actionable_rule": lesson.get("actionable_rule", ""),
            "prompt_injection_text": lesson.get("prompt_injection_text", ""),
            "ticker": lesson.get("ticker", ""),
            "sector": lesson.get("sector", "ALL"),
            "quarter": q_label,
            "created_at": created,
            "expires_at": expires_at,
            "expiry_quarters": expiry_q,
            "active": True,
        }
        if lesson.get("threshold_adjustment"):
            item["threshold_adjustment"] = lesson["threshold_adjustment"]
        if lesson.get("confidence_calibration"):
            item["confidence_calibration"] = lesson["confidence_calibration"]

        lessons_client._db.put_item(item)
        extracted.append(item)

    return extracted


def _apply_threshold_adjustments(
    lessons: list[dict[str, Any]],
    config_client: DynamoClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Phase 4: Auto-apply minor severity, flag moderate+ for review."""
    auto_applied: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []

    config_item = config_client.get_item({"config_key": "screening_thresholds"})
    current = (config_item.get("value") or {}) if config_item else {}
    if not isinstance(current, dict):
        current = {}

    for lesson in lessons:
        severity = (lesson.get("severity") or "").lower()
        adj = lesson.get("threshold_adjustment") or {}
        if not adj or not isinstance(adj, dict):
            continue
        param = adj.get("parameter")
        proposed = adj.get("proposed_value")
        if param is None or proposed is None:
            continue

        old_val = current.get(param)
        if old_val is not None:
            try:
                old_f = float(old_val)
                new_f = float(proposed)
                change_pct = abs(new_f - old_f) / max(abs(old_f), 1e-9)
                if change_pct > MAX_CHANGE_PCT_PER_QUARTER:
                    new_f = (
                        old_f * (1 + MAX_CHANGE_PCT_PER_QUARTER)
                        if new_f > old_f
                        else old_f * (1 - MAX_CHANGE_PCT_PER_QUARTER)
                    )
            except (TypeError, ValueError):
                new_f = proposed
        else:
            new_f = float(proposed)

        if severity == "minor":
            current[param] = new_f
            auto_applied.append({
                "parameter": param,
                "old_value": old_val,
                "new_value": new_f,
                "source": "post_mortem_auto",
                "lesson_id": lesson.get("lesson_id"),
            })
        else:
            flagged.append({
                "parameter": param,
                "proposed_value": proposed,
                "severity": severity,
                "lesson_id": lesson.get("lesson_id"),
            })

    if auto_applied:
        config_client.put_item({
            "config_key": "screening_thresholds",
            "value": current,
        })

    return auto_applied, flagged


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        year, quarter: optional override (default: current quarter)

    Output:
        letter_key, postmortem_key, decisions_audited, lessons_extracted,
        threshold_adjustments, auto_applied
    """
    cfg = get_config()
    now = datetime.now(UTC)
    year = int(event.get("year", now.year))
    quarter = int(event.get("quarter", (now.month - 1) // 3 + 1))
    q_label = f"Q{quarter}_{year}"

    decisions_client = DynamoClient(cfg.table_decisions)
    portfolio_client = DynamoClient(cfg.table_portfolio)
    config_client = DynamoClient(cfg.table_config)
    lessons_client = LessonsClient()
    llm_client = LLMClient()
    s3_client = S3Client()

    lessons_client.expire_stale_lessons()

    decision_audit, audit_summary = _run_outcome_audit(decisions_client, year, quarter)
    portfolio_summary = _load_portfolio_summary(portfolio_client)
    previous_lessons = _load_previous_lessons(lessons_client)

    letter_key, letter_md = _generate_letter(
        llm_client,
        s3_client,
        q_label,
        audit_summary,
        decision_audit,
        portfolio_summary,
        previous_lessons,
        year,
        quarter,
    )

    extracted_lessons = _extract_lessons(
        llm_client,
        lessons_client,
        letter_md,
        decision_audit,
        year,
        quarter,
    )

    auto_applied, flagged = _apply_threshold_adjustments(extracted_lessons, config_client)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    postmortem_key = f"postmortems/{year}/Q{quarter}_{date_str}.json"
    postmortem = {
        "quarter": q_label,
        "date": date_str,
        "audit_summary": audit_summary,
        "decision_audit": decision_audit,
        "lessons_extracted": extracted_lessons,
        "auto_applied": auto_applied,
        "flagged_for_review": flagged,
    }
    s3_client.write_json(postmortem_key, postmortem)

    return {
        "letter_key": letter_key,
        "postmortem_key": postmortem_key,
        "decisions_audited": len(decision_audit),
        "lessons_extracted": len(extracted_lessons),
        "threshold_adjustments": auto_applied + flagged,
        "auto_applied": auto_applied,
    }
