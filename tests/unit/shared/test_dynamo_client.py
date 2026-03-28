"""
Unit tests for DynamoClient.put_item_if_not_exists and condition_expression support.
"""

from __future__ import annotations

import pytest

from shared.dynamo_client import DynamoClient, ItemExistsError

TABLE_NAME = "omaha-oracle-dev-test"


@pytest.fixture()
def dynamo_table(aws_env: None, _moto_session):  # noqa: ARG001
    table = _moto_session.create_table(
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
    yield table
    table.delete()


def test_put_item_if_not_exists_returns_true_on_first_write(
    dynamo_table,
    monkeypatch,  # noqa: ARG001
):
    monkeypatch.setenv("TABLE_TEST", TABLE_NAME)
    client = DynamoClient(TABLE_NAME)
    item = {"pk": "DEDUP#AAPL#buy#2026-03-21", "sk": "DEDUP", "ticker": "AAPL"}
    result = client.put_item_if_not_exists(item)
    assert result is True


def test_put_item_if_not_exists_returns_false_on_duplicate(
    dynamo_table,
    monkeypatch,  # noqa: ARG001
):
    monkeypatch.setenv("TABLE_TEST", TABLE_NAME)
    client = DynamoClient(TABLE_NAME)
    item = {"pk": "DEDUP#AAPL#buy#2026-03-21", "sk": "DEDUP", "ticker": "AAPL"}
    client.put_item_if_not_exists(item)  # first write
    result = client.put_item_if_not_exists(item)  # second attempt
    assert result is False


def test_put_item_condition_expression_raises_item_exists_error(
    dynamo_table,
    monkeypatch,  # noqa: ARG001
):
    monkeypatch.setenv("TABLE_TEST", TABLE_NAME)
    client = DynamoClient(TABLE_NAME)
    item = {"pk": "TEST#1", "sk": "DATA", "value": "hello"}
    client.put_item(item)
    with pytest.raises(ItemExistsError):
        client.put_item(item, condition_expression="attribute_not_exists(pk)")


def test_put_item_without_condition_overwrites(
    dynamo_table,
    monkeypatch,  # noqa: ARG001
):
    monkeypatch.setenv("TABLE_TEST", TABLE_NAME)
    client = DynamoClient(TABLE_NAME)
    client.put_item({"pk": "TEST#2", "sk": "DATA", "value": "v1"})
    client.put_item({"pk": "TEST#2", "sk": "DATA", "value": "v2"})
    result = client.get_item({"pk": "TEST#2", "sk": "DATA"})
    assert result is not None
    assert result["value"] == "v2"
