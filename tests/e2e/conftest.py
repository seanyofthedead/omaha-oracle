"""E2E test fixtures — real external APIs + moto-backed AWS infrastructure.

The Anthropic SDK uses httpx (not requests), so it passes through moto's
responses-based mock automatically.  For SEC EDGAR and yfinance (which use
requests), we configure passthrough prefixes on the responses mock.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import boto3
import pytest
import requests
import responses as rsps
from moto import mock_aws

from dashboard.upload_validator import UploadMetadata

MARKEL_CIK = "1096343"
SEC_USER_AGENT = "OmahaOracle/1.0 test@omaha-oracle.test"

S3_BUCKET = "omaha-oracle-dev-data"
TABLES = {
    "cost": "omaha-oracle-dev-cost-tracking",
    "lessons": "omaha-oracle-dev-lessons",
    "config": "omaha-oracle-dev-config",
    "analysis": "omaha-oracle-dev-analysis",
    "companies": "omaha-oracle-dev-companies",
    "financials": "omaha-oracle-dev-financials",
}

# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------

requires_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

# ---------------------------------------------------------------------------
# Session-scoped: fetch real Markel 10-K from EDGAR once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def markel_10k_html() -> tuple[bytes, str]:
    """Download the most recent Markel Group 10-K (HTML) from EDGAR."""
    headers = {"User-Agent": SEC_USER_AGENT}
    resp = requests.get(
        f"https://data.sec.gov/submissions/CIK{MARKEL_CIK.zfill(10)}.json",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    recent = data["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            acc = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{MARKEL_CIK}/{acc}/{doc}"
            break
    else:
        pytest.skip("No Markel 10-K found in EDGAR submissions")

    time.sleep(0.2)  # SEC rate limiting
    resp = requests.get(url, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.content, doc


@pytest.fixture()
def markel_metadata() -> UploadMetadata:
    """Validated metadata for Markel Group 10-K FY 2024."""
    return UploadMetadata(
        ticker="MKL",
        company_name="Markel Group Inc.",
        filing_type="10-K",
        fiscal_year=2024,
    )


# ---------------------------------------------------------------------------
# Moto AWS tables + S3 bucket
# ---------------------------------------------------------------------------


def _create_all_tables(ddb, s3):
    """Create every DynamoDB table and S3 bucket the pipeline needs."""
    s3.create_bucket(Bucket=S3_BUCKET)

    # Cost tracking
    ddb.create_table(
        TableName=TABLES["cost"],
        KeySchema=[
            {"AttributeName": "month_key", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "month_key", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Lessons
    ddb.create_table(
        TableName=TABLES["lessons"],
        KeySchema=[
            {"AttributeName": "lesson_type", "KeyType": "HASH"},
            {"AttributeName": "lesson_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "lesson_type", "AttributeType": "S"},
            {"AttributeName": "lesson_id", "AttributeType": "S"},
            {"AttributeName": "active_flag", "AttributeType": "S"},
            {"AttributeName": "expires_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "active_flag-expires_at-index",
                "KeySchema": [
                    {"AttributeName": "active_flag", "KeyType": "HASH"},
                    {"AttributeName": "expires_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Config (seed MoS threshold)
    config_table = ddb.create_table(
        TableName=TABLES["config"],
        KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    config_table.put_item(Item={"config_key": "mos_threshold", "value": Decimal("0.30")})

    # Analysis
    ddb.create_table(
        TableName=TABLES["analysis"],
        KeySchema=[
            {"AttributeName": "ticker", "KeyType": "HASH"},
            {"AttributeName": "analysis_date", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "ticker", "AttributeType": "S"},
            {"AttributeName": "analysis_date", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Companies (seed Markel price data for IV calculation)
    companies = ddb.create_table(
        TableName=TABLES["companies"],
        KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    companies.put_item(
        Item={
            "ticker": "MKL",
            "currentPrice": Decimal("1850"),
            "marketCap": Decimal("25000000000"),
            "shares_outstanding": Decimal("13500000"),
        }
    )

    # Financials
    ddb.create_table(
        TableName=TABLES["financials"],
        KeySchema=[
            {"AttributeName": "ticker", "KeyType": "HASH"},
            {"AttributeName": "period", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "ticker", "AttributeType": "S"},
            {"AttributeName": "period", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture()
def e2e_tables(aws_env):
    """Spin up all DynamoDB tables + S3 bucket via moto."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        _create_all_tables(ddb, s3)
        yield {"ddb": ddb, "s3": s3}


@pytest.fixture()
def e2e_tables_with_passthrough(aws_env):
    """Like e2e_tables but allows non-AWS HTTP to pass through moto.

    Required for tests that call SEC EDGAR or yfinance from within mock_aws.
    """
    original = rsps.mock.passthru_prefixes
    rsps.mock.passthru_prefixes = original + (
        "https://api.anthropic.com",
        "https://data.sec.gov",
        "https://www.sec.gov",
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
        "https://fc.yahoo.com",
        "https://finance.yahoo.com",
    )
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        _create_all_tables(ddb, s3)
        yield {"ddb": ddb, "s3": s3}

    rsps.mock.passthru_prefixes = original


# ---------------------------------------------------------------------------
# Synthetic test files
# ---------------------------------------------------------------------------

MINIMAL_PDF = (
    b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000058 00000 n\n0000000115 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)

MINIMAL_XLSX = (
    # PK ZIP header + enough structure to look like an XLSX
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00" + b"\x00" * 18 + b"test.xlsx"
)
