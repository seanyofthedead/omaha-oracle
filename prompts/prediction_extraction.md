# Prediction Extraction

You are given an investment thesis for {{ticker}} ({{company_name}}) in the {{sector}} sector.

Extract exactly 3 falsifiable, quantitative predictions from this thesis. Each prediction must:
- Be measurable with a single numeric comparison
- Reference ONLY metrics from the allowed list below
- Have a specific deadline between 6 and 18 months from now ({{current_date}})
- Be grounded in specific claims made in the thesis

## Allowed Metrics

You MUST use one of these exact metric names:
- `revenue` — Annual revenue in USD
- `earnings_per_share` — Earnings per share in USD
- `gross_margin` — Gross margin as a decimal (e.g., 0.45 for 45%)
- `operating_margin` — Operating margin as a decimal
- `net_margin` — Net margin as a decimal
- `book_value_per_share` — Book value per share in USD
- `debt_to_equity` — Debt-to-equity ratio as a decimal
- `free_cash_flow` — Free cash flow in USD
- `return_on_equity` — Return on equity as a decimal
- `stock_price` — Stock price in USD

## Required Output

Return a JSON object with this exact structure:

```json
{
  "predictions": [
    {
      "description": "Plain-English description of what is predicted",
      "metric": "one of the allowed metric names above",
      "operator": ">" or "<" or ">=" or "<=" or "==",
      "threshold": 123.45,
      "data_source": "yahoo_finance" or "sec_edgar",
      "deadline": "YYYY-MM-DD"
    }
  ]
}
```

Rules:
- Return EXACTLY 3 predictions
- Each prediction must use a DIFFERENT metric
- Use `yahoo_finance` for: stock_price, revenue, earnings_per_share, book_value_per_share, debt_to_equity, free_cash_flow
- Use `sec_edgar` for: gross_margin, operating_margin, net_margin, return_on_equity
- Thresholds must be specific numbers, not ranges
- Deadlines must be valid ISO dates between 6 and 18 months from {{current_date}}
- Focus on the thesis's key assumptions — what must be true for the investment to work

## Thesis

{{thesis_markdown}}
