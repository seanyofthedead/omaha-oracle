import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

db = boto3.resource("dynamodb")
companies = db.Table("omaha-oracle-dev-companies")
financials = db.Table("omaha-oracle-dev-financials")

company = companies.get_item(Key={"ticker": "AAPL"}).get("Item", {})
print("=== COMPANY RECORD ===")
for k, v in sorted(company.items()):
    print(f"  {k}: {v}")

fins = financials.query(KeyConditionExpression=Key("ticker").eq("AAPL"))
items = fins.get("Items", [])
print(f"\n=== FINANCIALS: {len(items)} records ===")
metrics = set()
for f in items:
    metrics.add(f.get("metric_name", "?"))
print(f"Metrics available: {sorted(metrics)}")

# Show sample for each metric
for m in sorted(metrics):
    samples = [f for f in items if f.get("metric_name") == m][:2]
    for s in samples:
        print(f"  {m}: period={s.get('period')} value={s.get('value')}")
