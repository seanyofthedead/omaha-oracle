# Omaha Oracle — Operational Runbook

This runbook covers the six most critical operational procedures for a live Omaha Oracle deployment.

---

## 1. Emergency Kill Switch — Halt All Trading

**When to use:** Flash crash, erroneous signals, Alpaca issues, or any situation requiring an immediate trading stop.

```bash
# Disable trading
aws dynamodb put-item \
  --table-name omaha-oracle-prod-config \
  --item '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"},"value":{"BOOL":false}}' \
  --region us-east-1

# Verify
aws dynamodb get-item \
  --table-name omaha-oracle-prod-config \
  --key '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"}}' \
  --region us-east-1
```

**What to verify:** The allocation and execution Lambda handlers will log `"Trading is disabled via kill switch"` and return immediately without placing orders.

**To resume trading:**
```bash
aws dynamodb put-item \
  --table-name omaha-oracle-prod-config \
  --item '{"pk":{"S":"config"},"sk":{"S":"trading_enabled"},"value":{"BOOL":true}}' \
  --region us-east-1
```

---

## 2. Check Budget and LLM Spend

**When to use:** SNS budget alert fires, or you suspect unexpectedly high Anthropic spend.

```bash
# Scan current month's LLM cost records
aws dynamodb scan \
  --table-name omaha-oracle-prod-cost-tracking \
  --filter-expression "begins_with(month_key, :m)" \
  --expression-attribute-values '{":m":{"S":"2026-03"}}' \
  --region us-east-1 | jq '.Items[] | {module: .module.S, cost_usd: .cost_usd.N}' | sort

# Check via cost monitor Lambda directly
aws lambda invoke \
  --function-name omaha-oracle-prod-cost-monitor \
  --payload '{}' \
  --region us-east-1 \
  /tmp/cost-out.json && cat /tmp/cost-out.json
```

**What to verify:** `utilization_pct` in the response. If > 80%, an SNS alert should have fired. If > 100%, LLM calls for non-thesis tiers are being blocked.

**To raise the budget temporarily:**
```bash
aws ssm put-parameter \
  --name /omaha-oracle/prod/monthly-llm-budget-usd \
  --value "150.0" \
  --type String \
  --overwrite \
  --region us-east-1
```

---

## 3. Investigate a Failed Trade

**When to use:** SNS alert fires for a trade failure, or you notice a missing position after an expected execution.

```bash
# Check recent execution decisions
aws dynamodb query \
  --table-name omaha-oracle-prod-decisions \
  --key-condition-expression "begins_with(pk, :prefix)" \
  --expression-attribute-values '{":prefix":{"S":"DEDUP#"}}' \
  --scan-index-forward false \
  --limit 20 \
  --region us-east-1 | jq '.Items[] | {key: .pk.S, status: .status.S, order_id: .order_id.S}'

# Check CloudWatch logs for execution Lambda
aws logs tail /aws/lambda/omaha-oracle-prod-execution \
  --since 1h \
  --filter-pattern "ERROR" \
  --region us-east-1

# Check Alpaca order status (replace ORDER_ID)
curl -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
     -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
     https://api.alpaca.markets/v2/orders/ORDER_ID
```

**What to verify:** The dedup sentinel row exists. If `status` is missing, the order was written but Alpaca submit failed. Check CloudWatch for the specific error.

---

## 4. Sync Portfolio State After Manual Intervention

**When to use:** You manually adjusted positions in Alpaca, or the portfolio DynamoDB table is stale after an execution failure.

```bash
# Trigger a manual portfolio sync by invoking the execution Lambda with an empty orders list
aws lambda invoke \
  --function-name omaha-oracle-prod-execution \
  --payload '{"buy_orders":[],"sell_orders":[]}' \
  --region us-east-1 \
  /tmp/sync-out.json && cat /tmp/sync-out.json

# Verify portfolio table was updated
aws dynamodb get-item \
  --table-name omaha-oracle-prod-portfolio \
  --key '{"pk":{"S":"ACCOUNT"},"sk":{"S":"SUMMARY"}}' \
  --region us-east-1 | jq '.Item | {portfolio_value: .portfolio_value.N, cash_available: .cash_available.N, last_synced: .last_synced.S}'
```

**What to verify:** `last_synced` timestamp is current, `portfolio_value` and `cash_available` match what you see in the Alpaca dashboard.

---

## 5. Deploy an Update to Production

**When to use:** Merging a new feature or fix to `main` branch.

```bash
# 1. Ensure tests pass locally before pushing
pytest tests/unit/ -x -q
ruff check src/ tests/
mypy src/

# 2. Push to main — GitHub Actions will run tests automatically
git push origin main

# 3. Monitor the deploy workflow
gh run list --workflow deploy.yml --limit 5
gh run watch  # streams the active run

# 4. If the deploy job requires manual approval, approve via GitHub UI
#    or via CLI:
gh run approve RUN_ID

# 5. Verify after deploy
cdk diff --all -c env=prod  # should show no changes if deploy succeeded
```

**What to verify:** All CloudWatch Lambda error alarms are in OK state after deploy. Run a test invocation:
```bash
aws lambda invoke \
  --function-name omaha-oracle-prod-quant-screen \
  --payload '{}' \
  --region us-east-1 \
  /tmp/test-out.json && cat /tmp/test-out.json
```

---

## 6. Rotate API Keys

**When to use:** Suspected key compromise, scheduled rotation, or an API provider requires it.

```bash
# Update Anthropic API key in SSM (prod never uses env vars — always SSM)
aws ssm put-parameter \
  --name /omaha-oracle/prod/anthropic-api-key \
  --value "sk-ant-NEW-KEY-HERE" \
  --type SecureString \
  --overwrite \
  --region us-east-1

# Update Alpaca keys
aws ssm put-parameter \
  --name /omaha-oracle/prod/alpaca-api-key \
  --value "NEW-ALPACA-API-KEY" \
  --type SecureString \
  --overwrite \
  --region us-east-1

aws ssm put-parameter \
  --name /omaha-oracle/prod/alpaca-secret-key \
  --value "NEW-ALPACA-SECRET-KEY" \
  --type SecureString \
  --overwrite \
  --region us-east-1

# Update FRED key
aws ssm put-parameter \
  --name /omaha-oracle/prod/fred-api-key \
  --value "NEW-FRED-KEY" \
  --type SecureString \
  --overwrite \
  --region us-east-1
```

**What to verify:** Lambda functions use `lru_cache` for SSM values per invocation. The cache clears on the next cold start (typically within 15 minutes of no invocations, or after a deploy). To force immediate pickup:
```bash
# Touch the Lambda to force a cold start
aws lambda update-function-configuration \
  --function-name omaha-oracle-prod-execution \
  --description "key rotation $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --region us-east-1
```

Verify the new key works by invoking the function and confirming no `RuntimeError: ... not found in environment or SSM` errors in CloudWatch.
