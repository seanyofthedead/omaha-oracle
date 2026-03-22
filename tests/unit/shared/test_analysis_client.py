"""
Unit tests for shared.analysis_client — uses moto to mock DynamoDB.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from shared.analysis_client import load_latest_analysis, merge_latest_analysis

TABLE_NAME = "omaha-oracle-dev-analysis"


@pytest.fixture()
def analysis_table(aws_env: None):  # noqa: ARG001
    """Create moto analysis DynamoDB table."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
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
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield table


class TestMergeLatestAnalysis:
    def test_empty_items_returns_none(self) -> None:
        assert merge_latest_analysis([], "AAPL") is None

    def test_single_item(self) -> None:
        items = [
            {
                "ticker": "AAPL",
                "analysis_date": "2026-01-01#moat_analysis",
                "result": {"moat_score": 8, "sector": "Technology"},
            }
        ]
        result = merge_latest_analysis(items, "AAPL")
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["moat_score"] == 8
        assert result["sector"] == "Technology"

    def test_picks_latest_date(self) -> None:
        items = [
            {
                "ticker": "MSFT",
                "analysis_date": "2026-01-01#moat_analysis",
                "result": {"moat_score": 6},
            },
            {
                "ticker": "MSFT",
                "analysis_date": "2026-03-01#moat_analysis",
                "result": {"moat_score": 9},
            },
        ]
        result = merge_latest_analysis(items, "MSFT")
        assert result is not None
        assert result["moat_score"] == 9

    def test_merges_multiple_screens_on_same_date(self) -> None:
        items = [
            {
                "ticker": "GOOG",
                "analysis_date": "2026-03-01#moat_analysis",
                "result": {"moat_score": 7},
            },
            {
                "ticker": "GOOG",
                "analysis_date": "2026-03-01#management_assessment",
                "result": {"management_score": 8},
            },
        ]
        result = merge_latest_analysis(items, "GOOG")
        assert result is not None
        assert result["moat_score"] == 7
        assert result["management_score"] == 8

    def test_score_extracted_from_item_top_level_as_fallback(self) -> None:
        items = [
            {
                "ticker": "BRK",
                "analysis_date": "2026-03-01#moat_analysis",
                "moat_score": 9,
                "result": {},  # score not in result dict
            }
        ]
        result = merge_latest_analysis(items, "BRK")
        assert result is not None
        assert result["moat_score"] == 9

    def test_sector_defaults_to_unknown(self) -> None:
        items = [
            {
                "ticker": "XYZ",
                "analysis_date": "2026-03-01#moat_analysis",
                "result": {"moat_score": 5},
            }
        ]
        result = merge_latest_analysis(items, "XYZ")
        assert result is not None
        assert result["sector"] == "Unknown"

    def test_item_without_hash_in_sk(self) -> None:
        items = [
            {
                "ticker": "FOO",
                "analysis_date": "2026-03-01",
                "result": {"moat_score": 4},
            }
        ]
        result = merge_latest_analysis(items, "FOO")
        assert result is not None
        assert result["moat_score"] == 4


class TestLoadLatestAnalysis:
    def test_empty_table_returns_none(self, analysis_table) -> None:
        result = load_latest_analysis(TABLE_NAME, "AAPL")
        assert result is None

    def test_returns_merged_result(self, analysis_table) -> None:
        import os

        os.environ.setdefault("TABLE_ANALYSIS", TABLE_NAME)

        analysis_table.put_item(
            Item={
                "ticker": "AAPL",
                "analysis_date": "2026-03-01#moat_analysis",
                "result": {"moat_score": 8, "sector": "Technology"},
                "passed": True,
            }
        )
        analysis_table.put_item(
            Item={
                "ticker": "AAPL",
                "analysis_date": "2026-03-01#management_assessment",
                "result": {"management_score": 7},
                "passed": True,
            }
        )

        result = load_latest_analysis(TABLE_NAME, "AAPL")
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["moat_score"] == 8
        assert result["management_score"] == 7

    def test_unknown_ticker_returns_none(self, analysis_table) -> None:
        analysis_table.put_item(
            Item={
                "ticker": "MSFT",
                "analysis_date": "2026-03-01#moat_analysis",
                "result": {"moat_score": 7},
                "passed": True,
            }
        )

        result = load_latest_analysis(TABLE_NAME, "AAPL")
        assert result is None
