"""
Integration test: quant screen pipeline using moto-backed DynamoDB.

Verifies that the full quant screen → analysis table write flow works end-to-end
with a real (moto-mocked) DynamoDB, without any external HTTP calls.
"""

from __future__ import annotations

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from tests.conftest import TABLE_ANALYSIS, TABLE_COMPANIES, TABLE_CONFIG

TABLE_FINANCIALS = "omaha-oracle-dev-financials"
TABLE_WATCHLIST = "omaha-oracle-dev-watchlist"


@pytest.fixture()
def pipeline_tables(aws_env: None):  # noqa: ARG001
    """Spin up all tables needed for the quant screen pipeline."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")

        # Companies table: PK=ticker
        ddb.create_table(
            TableName=TABLE_COMPANIES,
            KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Financials table: PK=ticker, SK=period
        ddb.create_table(
            TableName=TABLE_FINANCIALS,
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

        # Analysis table: PK=ticker, SK=analysis_date
        ddb.create_table(
            TableName=TABLE_ANALYSIS,
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

        # Config table: PK=config_key
        ddb.create_table(
            TableName=TABLE_CONFIG,
            KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Watchlist table: PK=ticker
        ddb.create_table(
            TableName=TABLE_WATCHLIST,
            KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        yield ddb


def _seed_company(ddb, ticker: str) -> None:
    """Write a company row with quant-screen-friendly fundamentals."""
    table = ddb.Table(TABLE_COMPANIES)
    table.put_item(
        Item={
            "ticker": ticker,
            "trailingPE": Decimal("11.0"),  # < max_pe=15
            "priceToBook": Decimal("1.2"),  # < max_pb=1.5
            "marketCap": Decimal("1800000000000"),
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "bookValue": Decimal("120.0"),
            "trailingEps": Decimal("12.0"),
        }
    )


def _seed_financials(ddb, ticker: str) -> None:
    """Write 10 years of strong annual financial data."""
    table = ddb.Table(TABLE_FINANCIALS)
    for year in range(2015, 2025):
        period_end = f"{year}-09-30"
        metrics = {
            "revenue": 200_000_000_000 + year * 10_000_000_000,
            "net_income": 50_000_000_000 + year * 2_000_000_000,
            "total_assets": 300_000_000_000,
            "total_liabilities": 150_000_000_000,
            "stockholders_equity": 100_000_000_000,
            "long_term_debt": 30_000_000_000,
            "operating_cash_flow": 75_000_000_000 + year * 3_000_000_000,
            "capex": 10_000_000_000,
            "depreciation": 12_000_000_000,
            "current_assets": 150_000_000_000,
            "current_liabilities": 80_000_000_000,
            "shares_outstanding": 16_000_000_000,
        }
        for metric_name, value in metrics.items():
            table.put_item(
                Item={
                    "ticker": ticker,
                    "period": f"{period_end}#{metric_name}",
                    "period_end_date": period_end,
                    "metric_name": metric_name,
                    "value": Decimal(str(value)),
                }
            )


class TestParallelMergeDataFlow:
    """
    Data flow tests for the parallel pipeline: moat -> parallel(mgmt, iv) -> merge.

    These invoke the merge handler directly with realistic handler outputs,
    verifying the merged dict contains everything the thesis generator needs.
    """

    # Realistic moat output (input to both parallel branches)
    _MOAT_OUTPUT = {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "metrics": {"revenue": 400_000_000_000, "pe_ratio": 11.0},
        "quant_result": {"pass": True, "piotroski_f": 7},
        "moat_score": 8,
        "moat_type": "wide",
        "moat_sources": ["switching costs", "intangible assets"],
        "moat_trend": "stable",
        "pricing_power": 7,
        "customer_captivity": 8,
        "reasoning": "Apple has exceptional switching costs and brand power.",
        "risks_to_moat": ["commoditisation of smartphones"],
        "confidence": 0.85,
        "skipped": False,
        "filing_context_degraded": False,
        "cost_usd": 0.01,
    }

    # Simulated management handler output (moat output + management fields)
    _MGMT_OUTPUT = {
        **_MOAT_OUTPUT,
        "management_score": 7,
        "owner_operator_mindset": 8,
        "capital_allocation_skill": 7,
        "candor_transparency": 6,
        "red_flags": [],
        "green_flags": ["strong buyback program"],
    }

    # Simulated IV handler output (moat output + IV fields)
    _IV_OUTPUT = {
        **_MOAT_OUTPUT,
        "intrinsic_value_per_share": 210.0,
        "margin_of_safety": 0.45,
        "buy_signal": True,
        "scenarios": {"base": {"iv": 210}, "bull": {"iv": 260}, "bear": {"iv": 160}},
        "dcf_per_share": 220.0,
        "epv_per_share": 200.0,
        "floor_per_share": 180.0,
        "mos_threshold": 0.30,
        "current_price": 145.0,
    }

    # All fields the thesis generator requires
    THESIS_REQUIRED_FIELDS = [
        "ticker",
        "company_name",
        "metrics",
        "quant_result",
        "moat_score",
        "moat_type",
        "moat_sources",
        "management_score",
        "owner_operator_mindset",
        "capital_allocation_skill",
        "candor_transparency",
        "intrinsic_value_per_share",
        "margin_of_safety",
        "buy_signal",
        "scenarios",
        "dcf_per_share",
        "epv_per_share",
        "floor_per_share",
        "mos_threshold",
    ]

    def test_merge_produces_all_thesis_required_fields(self):
        """Merge of mgmt + IV outputs contains every field thesis generator needs."""
        from analysis.merge_results.handler import handler as merge_handler

        merged = merge_handler([self._MGMT_OUTPUT, self._IV_OUTPUT], None)

        for field in self.THESIS_REQUIRED_FIELDS:
            assert field in merged, f"Missing thesis-required field: {field}"

    def test_merge_preserves_management_fields(self):
        """Management-specific fields survive the merge."""
        from analysis.merge_results.handler import handler as merge_handler

        merged = merge_handler([self._MGMT_OUTPUT, self._IV_OUTPUT], None)

        assert merged["management_score"] == 7
        assert merged["owner_operator_mindset"] == 8
        assert merged["capital_allocation_skill"] == 7
        assert merged["candor_transparency"] == 6
        assert merged["red_flags"] == []
        assert merged["green_flags"] == ["strong buyback program"]

    def test_merge_preserves_iv_fields(self):
        """IV-specific fields survive the merge."""
        from analysis.merge_results.handler import handler as merge_handler

        merged = merge_handler([self._MGMT_OUTPUT, self._IV_OUTPUT], None)

        assert merged["intrinsic_value_per_share"] == 210.0
        assert merged["margin_of_safety"] == 0.45
        assert merged["buy_signal"] is True
        assert merged["dcf_per_share"] == 220.0
        assert merged["epv_per_share"] == 200.0
        assert merged["floor_per_share"] == 180.0
        assert merged["mos_threshold"] == 0.30

    def test_overlapping_passthrough_keys_are_consistent(self):
        """Both branches pass through identical moat fields — merge is safe."""
        from analysis.merge_results.handler import handler as merge_handler

        merged = merge_handler([self._MGMT_OUTPUT, self._IV_OUTPUT], None)

        assert merged["ticker"] == "AAPL"
        assert merged["company_name"] == "Apple Inc."
        assert merged["moat_score"] == 8
        assert merged["metrics"] == self._MOAT_OUTPUT["metrics"]
        assert merged["quant_result"] == self._MOAT_OUTPUT["quant_result"]

    def test_single_branch_output_passes_through(self):
        """If only one branch output exists (simulating failure), merge returns it."""
        from analysis.merge_results.handler import handler as merge_handler

        merged = merge_handler([self._MGMT_OUTPUT], None)
        assert merged == self._MGMT_OUTPUT


class TestQuantScreenPipeline:
    """Integration tests for the quant screen → analysis table write flow."""

    def test_qualifying_company_writes_analysis_row(self, pipeline_tables, monkeypatch):
        monkeypatch.setenv("TABLE_FINANCIALS", TABLE_FINANCIALS)
        monkeypatch.setenv("TABLE_WATCHLIST", TABLE_WATCHLIST)

        _seed_company(pipeline_tables, "AAPL")
        _seed_financials(pipeline_tables, "AAPL")

        from analysis.quant_screen.handler import handler

        result = handler({}, None)

        assert result["total_screened"] == 1

        # Verify analysis row was written to DynamoDB
        analysis_table = pipeline_tables.Table(TABLE_ANALYSIS)
        resp = analysis_table.scan()
        items = resp.get("Items", [])
        assert len(items) == 1
        item = items[0]
        assert item["ticker"] == "AAPL"
        assert item["screen_type"] == "quant_screen"

    def test_company_with_no_financials_is_skipped(self, pipeline_tables, monkeypatch):
        monkeypatch.setenv("TABLE_FINANCIALS", TABLE_FINANCIALS)
        monkeypatch.setenv("TABLE_WATCHLIST", TABLE_WATCHLIST)

        # Seed company but no financials
        _seed_company(pipeline_tables, "NOFIN")

        from analysis.quant_screen.handler import handler

        result = handler({}, None)

        assert result["total_screened"] == 1
        assert result["total_passed"] == 0

        # No analysis row written for companies with no financials
        analysis_table = pipeline_tables.Table(TABLE_ANALYSIS)
        resp = analysis_table.scan()
        assert resp.get("Items", []) == []

    def test_two_companies_both_screened(self, pipeline_tables, monkeypatch):
        monkeypatch.setenv("TABLE_FINANCIALS", TABLE_FINANCIALS)
        monkeypatch.setenv("TABLE_WATCHLIST", TABLE_WATCHLIST)

        _seed_company(pipeline_tables, "AAPL")
        _seed_financials(pipeline_tables, "AAPL")
        _seed_company(pipeline_tables, "MSFT")
        _seed_financials(pipeline_tables, "MSFT")

        from analysis.quant_screen.handler import handler

        result = handler({}, None)

        assert result["total_screened"] == 2

        analysis_table = pipeline_tables.Table(TABLE_ANALYSIS)
        items = analysis_table.scan().get("Items", [])
        tickers = {item["ticker"] for item in items}
        assert tickers == {"AAPL", "MSFT"}
