import sys

sys.path.insert(0, "src")
import boto3

from shared.config import get_config

config = get_config()
db = boto3.resource("dynamodb")
companies = db.Table(config.table_companies)

items = companies.scan().get("Items", [])
print(f"{'Ticker':<8} {'P/E':>8} {'P/B':>8} {'PE<25':>6} {'PB<5':>6}")
print("-" * 42)
for c in sorted(items, key=lambda x: x.get("ticker", "")):
    ticker = c.get("ticker", "?")
    pe = float(c.get("trailingPE", 0))
    pb = float(c.get("priceToBook", 0))
    pe_ok = "PASS" if 0 < pe < 25 else "FAIL"
    pb_ok = "PASS" if 0 < pb < 5 else "FAIL"
    print(f"{ticker:<8} {pe:>8.1f} {pb:>8.1f} {pe_ok:>6} {pb_ok:>6}")
