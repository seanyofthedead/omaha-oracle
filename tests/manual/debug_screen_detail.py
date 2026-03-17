import sys
sys.path.insert(0, "src")
from shared.config import get_config
from shared.dynamo_client import DynamoClient
from analysis.quant_screen.handler import _screen_company

config = get_config()
companies_db = DynamoClient(config.table_companies)
financials_db = DynamoClient(config.table_financials)
config_db = DynamoClient(config.table_config)

thresholds = config_db.get_item({"config_key": "screening_thresholds"})
print("Thresholds:", {k:v for k,v in thresholds.items() if k != "config_key"})

items = companies_db.scan_all()
for company in sorted(items, key=lambda x: x.get("ticker", "")):
    ticker = company.get("ticker", "?")
    try:
        result = _screen_company(ticker, company, financials_db, thresholds, True)
        print(f"\n{ticker}: {result}")
    except Exception as e:
        import traceback
        print(f"\n{ticker}: ERROR - {e}")
        traceback.print_exc()
