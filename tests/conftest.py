"""
Shared pytest fixtures for the Omaha Oracle test suite.

Fixture dependency graph (for DynamoDB tests):
  reset_config (autouse) ──► all tests
  _moto_session (session) ──► dynamodb_table / lessons_table / iv_tables
  aws_env ──► dynamodb_table ──► cost_tracker

Performance: A single session-scoped mock_aws context is shared across all
tests.  Each function-scoped fixture creates a fresh table and deletes it on
teardown (~17 ms), avoiding the ~7 s cold-start of a new mock_aws context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The table name that Settings._apply_defaults produces for ENVIRONMENT=dev
TABLE_NAME = "omaha-oracle-dev-cost-tracking"
TABLE_LESSONS = "omaha-oracle-dev-lessons"
TABLE_DECISIONS = "omaha-oracle-dev-decisions"
TABLE_CONFIG = "omaha-oracle-dev-config"
TABLE_ANALYSIS = "omaha-oracle-dev-analysis"
TABLE_COMPANIES = "omaha-oracle-dev-companies"


# ------------------------------------------------------------------ #
# Collection hooks                                                     #
# ------------------------------------------------------------------ #


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip tests/unit/infra/ unless explicitly requested via ``-m infra``.

    Importing ``aws_cdk`` takes ~60 s.  By skipping the infra directory during
    normal collection, we avoid that penalty for the 99 % of runs that don't
    need CDK tests.  Run them explicitly with::

        pytest tests/unit/infra/ -m infra
    """
    if "infra" in collection_path.parts:
        marker_expr = config.getoption("-m", default="")
        if "infra" not in str(marker_expr):
            return True
    return None


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
# Session-scoped moto context                                         #
# ------------------------------------------------------------------ #


@pytest.fixture(scope="session")
def _moto_session():
    """Single mock_aws context shared across the entire test session.

    Starting a new ``mock_aws()`` context + creating the first
    ``boto3.resource`` takes ~7 s.  By doing it once and reusing the resource,
    every subsequent table creation drops to ~17 ms.
    """
    import boto3
    from moto import mock_aws

    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


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
def dynamodb_table(aws_env: None, _moto_session):
    """
    Create an in-memory DynamoDB cost-tracking table using the shared moto
    session.  The table is deleted on teardown so the next test gets a clean
    slate.

    Schema mirrors the production table:
      PK  month_key  (S)
      SK  timestamp  (S)
    """
    table = _moto_session.create_table(
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
    yield table
    table.delete()


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
def lessons_table(aws_env: None, _moto_session):
    """Create lessons DynamoDB table for LessonsClient tests."""
    table = _moto_session.create_table(
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
    yield table
    table.delete()


@pytest.fixture()
def lessons_client(lessons_table):  # noqa: ARG001
    """Return a LessonsClient bound to the moto lessons table."""
    from shared.lessons_client import LessonsClient

    return LessonsClient(table_name=TABLE_LESSONS)


@pytest.fixture()
def iv_tables(aws_env: None, _moto_session):
    """
    Spin up config, analysis, and companies DynamoDB tables for intrinsic
    value handler tests.  Yields the boto3 dynamodb resource so tests can
    seed data directly via iv_tables.Table(TABLE_CONFIG).put_item(...).
    """
    tables = []
    # Config table: PK=config_key
    tables.append(
        _moto_session.create_table(
            TableName=TABLE_CONFIG,
            KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
    )
    # Analysis table: PK=ticker, SK=analysis_date
    tables.append(
        _moto_session.create_table(
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
    )
    # Companies table: PK=ticker (for _resolve_metrics fallback)
    tables.append(
        _moto_session.create_table(
            TableName=TABLE_COMPANIES,
            KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
    )
    yield _moto_session
    for t in tables:
        t.delete()
