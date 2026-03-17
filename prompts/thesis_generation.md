# Investment Thesis Generation

Write a Buffett-style investment thesis for {{company_name}} ({{ticker}}) in plain English. Target length: 1–2 pages. Output valid markdown only.

## Input Summary

**Quantitative screen**: {{quant_summary}}

**Moat analysis** (score {{moat_score}}/10): {{moat_summary}}

**Management assessment** (score {{management_score}}/10): {{management_summary}}

**Valuation**: {{valuation_summary}}

---

## Required Structure

Write the thesis with exactly these six sections. Use `##` for section headers.

### 1. The Business
Explain what the company does in plain English. How does it make money? Who are its customers? What is the industry structure?

### 2. The Moat
What are the specific competitive advantages? Be concrete. Reference the moat analysis findings.

### 3. Management
Owner-operator mindset, capital allocation, candor. What stands out positively or negatively?

### 4. Valuation
Summarise the math: DCF scenarios, EPV, asset floor, composite intrinsic value, margin of safety. Show the numbers.

### 5. Kill the Thesis
What could go wrong? What would trigger a sell? Be specific.

### 6. The Verdict
- Buy price (or price range)
- Position size recommendation (as % of portfolio)
- One-sentence summary

---

Output markdown only. No JSON, no preamble.
