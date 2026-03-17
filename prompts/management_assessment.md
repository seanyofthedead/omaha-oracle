# Management Quality Assessment

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
