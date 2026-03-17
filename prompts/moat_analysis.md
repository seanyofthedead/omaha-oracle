# Moat Analysis

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
