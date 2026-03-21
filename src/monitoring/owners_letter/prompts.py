"""
Prompt templates for the owners letter post-mortem pipeline.
"""

OWNERS_LETTER_PROMPT = """# Owner's Letter — Quarterly Post-Mortem

You are writing a brutally honest quarterly letter to yourself,
as a value investor holding yourself accountable. Channel Warren
Buffett's candor in his annual letters: no excuses, no spin,
only truth.

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
- **Ticker** | **Signal** (BUY/SELL/NO_BUY) | **Date** |
**Price at decision** | **Current price** |
**Outcome** (e.g. GOOD_BUY, BAD_BUY, MISSED_OPPORTUNITY)
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
- Usable in future analysis (e.g. "Reduce moat confidence for
companies with >60% revenue from single customer")

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

You are a quantitative analyst extracting structured lessons
from a quarterly Owner's Letter and decision audit.

## Task

Extract **one lesson per BAD_BUY, BAD_SELL, and
MISSED_OPPORTUNITY** from the letter. For GOOD decisions,
extract a lesson ONLY if the reasoning was flawed
(lucky outcome).

## Lesson Schema (each lesson MUST have)

- **lesson_id** (string): Format "Q{N}_{year}_{index}", e.g. "Q1_2026_1"
- **lesson_type** (string): One of moat_bias | valuation_bias
| management_bias | sector_bias | threshold_adjustment
| process_improvement | data_quality
- **severity** (string): minor | moderate | high | critical
- **description** (string): 1-2 sentences describing what went wrong
- **actionable_rule** (string): Specific rule for future
analysis, e.g. "When revenue concentration >60%,
reduce moat score by 2"
- **prompt_injection_text** (string): 2-3 sentences for
future prompts — what should analysts see when evaluating
similar situations
- **ticker** (string, optional): Ticker if lesson is company-specific, else ""
- **sector** (string, optional): Sector if lesson is sector-specific, else "ALL"
- **expiry_quarters** (integer): 4-12, how many quarters this lesson stays active before review

## Optional (include when applicable)

- **threshold_adjustment** (object):
{ "parameter": string, "proposed_value": number,
"scope": string }
- **confidence_calibration** (object):
{ "analysis_stage": string,
"bias_direction": "over"|"under",
"adjustment_factor": number 0.7-1.3 }

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
      "confidence_calibration": {
        "analysis_stage": "moat_analysis",
        "bias_direction": "over",
        "adjustment_factor": 0.85
      }
    }
  ]
}
```
"""
