"""
Shared pytest fixtures for the Omaha Oracle test suite.

Fixture dependency graph (for DynamoDB tests):
  reset_config (autouse) ──► all tests
  aws_env ──► dynamodb_table ──► cost_tracker
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

# The table name that Settings._apply_defaults produces for ENVIRONMENT=dev
TABLE_NAME = "omaha-oracle-dev-cost-tracking"
TABLE_LESSONS = "omaha-oracle-dev-lessons"
TABLE_DECISIONS = "omaha-oracle-dev-decisions"
TABLE_CONFIG = "omaha-oracle-dev-config"
TABLE_ANALYSIS = "omaha-oracle-dev-analysis"
TABLE_COMPANIES = "omaha-oracle-dev-companies"


# ------------------------------------------------------------------ #
# Config isolation                                                    #
# ------------------------------------------------------------------ #


@pytest.fixture(autouse=True)
def reset_config():
    """
    Clear the get_config() LRU cache and the module-level SSM dict before and
    after every test.  Without this, a monkeypatched env var in test A would
    have no effect in test B because the cached Settings instance is reused.
    """
    from shared.config import _ssm_get, get_config

    _ssm_get.cache_clear()
    get_config.cache_clear()
    yield
    get_config.cache_clear()
    _ssm_get.cache_clear()


# ------------------------------------------------------------------ #
# AWS / environment fixtures                                          #
# ------------------------------------------------------------------ #


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Inject fake AWS credentials and deterministic config values so that:
      - boto3 never reaches real AWS (moto intercepts at the HTTP layer)
      - get_config() returns a stable Settings object regardless of .env

    Setting MONTHLY_LLM_BUDGET_CENTS=0 disables the legacy-cents override in
    Settings._apply_defaults so MONTHLY_LLM_BUDGET_USD is the sole source of
    truth for the budget in all standard tests.

    Removing AWS_PROFILE prevents boto3 from trying to resolve a named profile
    when the explicit key/secret env vars are already set.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
    monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
    monkeypatch.setenv("TABLE_COST_TRACKING", TABLE_NAME)
    monkeypatch.setenv("TABLE_LESSONS", TABLE_LESSONS)
    monkeypatch.setenv("TABLE_DECISIONS", TABLE_DECISIONS)
    monkeypatch.setenv("TABLE_CONFIG", TABLE_CONFIG)


# ------------------------------------------------------------------ #
# DynamoDB fixtures                                                   #
# ------------------------------------------------------------------ #


@pytest.fixture()
def dynamodb_table(aws_env: None):
    """
    Spin up an in-memory DynamoDB cost-tracking table using moto.

    The mock_aws context intercepts every boto3 call for the duration of the
    test, so no real AWS traffic is ever made.  A fresh table is created for
    each test — no shared mutable state.

    Schema mirrors the production table:
      PK  month_key  (S)
      SK  timestamp  (S)
    """
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
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
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        yield table


@pytest.fixture()
def cost_tracker(dynamodb_table):  # noqa: ARG001
    """
    Return a CostTracker bound to the moto table.

    Requesting dynamodb_table ensures the mock_aws context is active and the
    table exists before CostTracker.__init__ calls boto3.resource("dynamodb").
    """
    from shared.cost_tracker import CostTracker

    return CostTracker(table_name=TABLE_NAME)


@pytest.fixture()
def lessons_table(aws_env: None):
    """Create lessons DynamoDB table for LessonsClient tests."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=TABLE_LESSONS,
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
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_LESSONS)
        yield table


@pytest.fixture()
def lessons_client(lessons_table):  # noqa: ARG001
    """Return a LessonsClient bound to the moto lessons table."""
    from shared.lessons_client import LessonsClient

    return LessonsClient(table_name=TABLE_LESSONS)


@pytest.fixture()
def iv_tables(aws_env: None):
    """
    Spin up config, analysis, and companies DynamoDB tables for intrinsic
    value handler tests.  Yields the boto3 dynamodb resource so tests can
    seed data directly via iv_tables.Table(TABLE_CONFIG).put_item(...).
    """
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        # Config table: PK=config_key
        ddb.create_table(
            TableName=TABLE_CONFIG,
            KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
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
        # Companies table: PK=ticker (for _resolve_metrics fallback)
        ddb.create_table(
            TableName=TABLE_COMPANIES,
            KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb
