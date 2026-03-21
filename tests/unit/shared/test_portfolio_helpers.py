"""
Unit tests for shared.portfolio_helpers — uses moto to mock DynamoDB.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from shared.portfolio_helpers import load_portfolio_state

TABLE_NAME = "omaha-oracle-dev-portfolio"


@pytest.fixture()
def portfolio_table(aws_env: None):  # noqa: ARG001
    """Create moto portfolio DynamoDB table (composite key pk+sk)."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield table


class TestLoadPortfolioState:
    def test_empty_table_returns_zeros(self, portfolio_table) -> None:
        state = load_portfolio_state(TABLE_NAME)
        assert state["portfolio_value"] == 0.0
        assert state["cash_available"] == 0.0
        assert state["positions"] == []
        assert state["sector_exposure"] == {}

    def test_loads_account_summary(self, portfolio_table) -> None:
        portfolio_table.put_item(
            Item={
                "pk": "ACCOUNT",
                "sk": "SUMMARY",
                "cash_available": "50000.00",
                "portfolio_value": "150000.00",
            }
        )

        state = load_portfolio_state(TABLE_NAME)
        assert state["cash_available"] == pytest.approx(50000.0)
        assert state["portfolio_value"] == pytest.approx(150000.0)

    def test_loads_positions(self, portfolio_table) -> None:
        portfolio_table.put_item(
            Item={
                "pk": "ACCOUNT",
                "sk": "SUMMARY",
                "cash_available": "10000",
                "portfolio_value": "60000",
            }
        )
        portfolio_table.put_item(
            Item={
                "pk": "POSITION",
                "sk": "AAPL",
                "market_value": "50000",
                "shares": "250",
                "sector": "Technology",
                "cost_basis": "40000",
            }
        )

        state = load_portfolio_state(TABLE_NAME)
        assert len(state["positions"]) == 1
        pos = state["positions"][0]
        assert pos["ticker"] == "AAPL"
        assert pos["market_value"] == pytest.approx(50000.0)
        assert pos["shares"] == pytest.approx(250.0)
        assert pos["sector"] == "Technology"

    def test_computes_sector_exposure(self, portfolio_table) -> None:
        portfolio_table.put_item(
            Item={
                "pk": "ACCOUNT",
                "sk": "SUMMARY",
                "cash_available": "0",
                "portfolio_value": "100000",
            }
        )
        portfolio_table.put_item(
            Item={
                "pk": "POSITION",
                "sk": "AAPL",
                "market_value": "60000",
                "shares": "300",
                "sector": "Technology",
            }
        )
        portfolio_table.put_item(
            Item={
                "pk": "POSITION",
                "sk": "JPM",
                "market_value": "40000",
                "shares": "200",
                "sector": "Financials",
            }
        )

        state = load_portfolio_state(TABLE_NAME)
        assert state["sector_exposure"]["Technology"] == pytest.approx(0.6)
        assert state["sector_exposure"]["Financials"] == pytest.approx(0.4)

    def test_portfolio_value_derived_from_positions_when_zero(self, portfolio_table) -> None:
        # No ACCOUNT row → cash=0, total=0 → derive from positions
        portfolio_table.put_item(
            Item={
                "pk": "POSITION",
                "sk": "GOOG",
                "market_value": "30000",
                "shares": "15",
                "sector": "Technology",
            }
        )

        state = load_portfolio_state(TABLE_NAME)
        assert state["portfolio_value"] == pytest.approx(30000.0)
