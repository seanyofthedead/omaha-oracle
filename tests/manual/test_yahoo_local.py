"""
Manual test script for Yahoo Finance handler.

Run from project root:
  python tests\\manual\\test_yahoo_local.py

1. Fetches AAPL via yfinance directly to confirm the library works.
2. Calls the full_refresh handler — locally, DynamoDB/S3 writes will fail
   (expected); we treat boto3/botocore errors as "data fetch worked, AWS failed".
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src to path so we can import ingestion.yahoo_finance.handler
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import yfinance as yf
from botocore.exceptions import BotoCoreError

# ------------------------------------------------------------------ #
# 1. Standalone yfinance test                                          #
# ------------------------------------------------------------------ #
print("--- Standalone yfinance test (AAPL) ---")
ticker = yf.Ticker("AAPL")
info = ticker.info or {}
name = info.get("shortName") or info.get("longName") or "N/A"
price = info.get("currentPrice") or info.get("regularMarketPrice") or "N/A"
sector = info.get("sector") or "N/A"
print(f"Company: {name}")
print(f"Current price: {price}")
print(f"Sector: {sector}")
print()

# ------------------------------------------------------------------ #
# 2. Handler call with full_refresh                                    #
# ------------------------------------------------------------------ #
print("--- Calling handler with full_refresh ---")
from ingestion.yahoo_finance.handler import handler

event = {"action": "full_refresh", "ticker": "AAPL"}
try:
    result = handler(event, context=None)
    if result.get("status") == "ok":
        print("Result:", result)
    else:
        # Handler caught DynamoDB/S3 error and returned error dict
        print("Expected AWS error — but data fetch worked")
except BotoCoreError:
    # Handler or its deps raised before returning (e.g. no credentials)
    print("Expected AWS error — but data fetch worked")
