"""
Fixtures for integration tests — multi-table DynamoDB, mocked external services.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from tests.conftest import (
    TABLE_CONFIG,
    TABLE_DECISIONS,
    TABLE_LESSONS,
    aws_env,
)


@pytest.fixture()
def integration_tables(aws_env: None):
    """Create decisions, lessons, and config tables for feedback loop integration."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")

        decisions = ddb.create_table(
            TableName=TABLE_DECISIONS,
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
        decisions.meta.client.get_waiter("table_exists").wait(TableName=TABLE_DECISIONS)

        lessons = ddb.create_table(
            TableName=TABLE_LESSONS,
            KeySchema=[
                {"AttributeName": "lesson_type", "KeyType": "HASH"},
                {"AttributeName": "lesson_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "lesson_type", "AttributeType": "S"},
                {"AttributeName": "lesson_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        lessons.meta.client.get_waiter("table_exists").wait(TableName=TABLE_LESSONS)

        config = ddb.create_table(
            TableName=TABLE_CONFIG,
            KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        config.meta.client.get_waiter("table_exists").wait(TableName=TABLE_CONFIG)

        yield {"decisions": decisions, "lessons": lessons, "config": config}
