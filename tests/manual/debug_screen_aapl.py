import sys

sys.path.insert(0, "src")

import boto3

from shared.config import get_config

config = get_config()
db = boto3.resource("dynamodb")
companies_table = db.Table(config.table_companies)
financials_table = db.Table(config.table_financials)
config_table = db.Table(config.table_config)

# Load thresholds
th_item = config_table.get_item(Key={"config_key": "screening_thresholds"}).get("Item", {})
print("=== THRESHOLDS ===")
for k, v in sorted(th_item.items()):
    if k != "config_key":
        print(f"  {k}: {v}")

# Get AAPL
company = companies_table.get_item(Key={"ticker": "AAPL"}).get("Item", {})
pe = float(company.get("trailingPE", 0))
pb = float(company.get("priceToBook", 0))
print("\n=== AAPL RAW VALUES ===")
print(f"  P/E: {pe}")
print(f"  P/B: {pb}")
pe_max = float(th_item.get("max_pe", 25))
pe_pass = pe > 0 and pe < pe_max
print(f"  P/E pass (<{th_item.get('max_pe', '?')}): {pe_pass}")
print(
    f"  P/B pass (<{th_item.get('max_pb', '?')}): {pb > 0 and pb < float(th_item.get('max_pb', 5))}"
)

# Try calling the actual screen
try:
    from analysis.quant_screen import handler as qsh

    # Find the compute and screen functions
    funcs = [
        f
        for f in dir(qsh)
        if "compute" in f.lower()
        or "screen" in f.lower()
        or "pass" in f.lower()
        or "metric" in f.lower()
    ]
    print("\n=== AVAILABLE FUNCTIONS ===")
    print(f"  {funcs}")
except Exception as e:
    print(f"\nImport error: {e}")
