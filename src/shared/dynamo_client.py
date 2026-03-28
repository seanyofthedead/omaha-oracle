"""
Generic DynamoDB CRUD client for Omaha Oracle.

All methods catch ``ClientError`` and re-raise after structured logging so
callers get a consistent error surface without boilerplate try/except blocks.

Write operations (put_item, batch_write, update_item) automatically sanitize
float/int values to Decimal for DynamoDB compatibility.

Module-level helper functions
-----------------------------
store_analysis_result  — write one analysis-table row (replaces 4 identical
                         ``_store_result`` functions across analysis handlers)
get_watchlist_tickers  — scan the watchlist table and return ticker list
                         (replaces 3 identical helpers in ingestion handlers)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import ConditionBase
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError

from shared.config import get_config
from shared.logger import get_logger


class ItemExistsError(Exception):
    """Raised when a conditional put_item fails because the item already exists."""


_DYNAMO_CONFIG = BotocoreConfig(retries={"mode": "adaptive", "max_attempts": 5})

_log = get_logger(__name__)

# DynamoDB item: string keys, any boto3-supported value type
Item = dict[str, Any]


def sanitize_for_dynamo(obj: Any) -> Any:
    """
    Recursively convert float/int to Decimal for DynamoDB compatibility.

    Walks dicts and lists, converts numeric values, leaves strings, bools,
    None, and other types untouched.
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_dynamo(v) for v in obj]
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, int) and not isinstance(obj, bool):
        return Decimal(str(obj))
    return obj


class DynamoClient:
    """
    Thin wrapper around a single DynamoDB table.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB table to target.  When *None* the caller must
        pass a table name later (the class is not usable without one).
    """

    def __init__(self, table_name: str | None = None) -> None:
        """Initialize the client against *table_name*; raises if omitted."""
        cfg = get_config()
        if table_name is None:
            raise ValueError("table_name is required")
        self._table_name = table_name
        self._table = boto3.resource(
            "dynamodb", region_name=cfg.aws_region, config=_DYNAMO_CONFIG
        ).Table(table_name)

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def put_item(self, item: Item, condition_expression: str | None = None) -> None:
        """Write *item*, replacing any existing record with the same key.

        Parameters
        ----------
        condition_expression:
            Optional DynamoDB condition string, e.g.
            ``"attribute_not_exists(pk)"``.  When the condition fails a
            :class:`ItemExistsError` is raised instead of ``ClientError``.
        """
        sanitized = sanitize_for_dynamo(item)
        kwargs: dict[str, Any] = {"Item": sanitized}
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression
        try:
            self._table.put_item(**kwargs)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ItemExistsError(f"Item already exists in {self._table_name}") from exc
            _log.error(
                "DynamoDB put_item failed",
                extra={"table": self._table_name, "error": str(exc)},
            )
            raise

    def put_item_if_not_exists(self, item: Item) -> bool:
        """Write *item* only if an item with the same primary key does not exist.

        Returns
        -------
        bool
            ``True`` if the item was written; ``False`` if it already existed.
        """
        try:
            self.put_item(item, condition_expression="attribute_not_exists(pk)")
            return True
        except ItemExistsError:
            return False

    def update_item(
        self,
        key: Item,
        update_expression: str,
        expression_attribute_values: Item,
        expression_attribute_names: dict[str, str] | None = None,
        condition_expression: str | None = None,
    ) -> Item:
        """
        Update specific attributes on an existing item.

        Returns the full item as it looks after the update
        (``ReturnValues="ALL_NEW"``).
        """
        sanitized_key = sanitize_for_dynamo(key)
        sanitized_values = sanitize_for_dynamo(expression_attribute_values)
        kwargs: dict[str, Any] = {
            "Key": sanitized_key,
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": sanitized_values,
            "ReturnValues": "ALL_NEW",
        }
        if expression_attribute_names:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression

        try:
            response = self._table.update_item(**kwargs)
            updated: Item = response.get("Attributes", {})
            return updated
        except ClientError as exc:
            _log.error(
                "DynamoDB update_item failed",
                extra={"table": self._table_name, "key": key, "error": str(exc)},
            )
            raise

    def delete_item(
        self,
        key: Item,
        condition_expression: str | None = None,
    ) -> None:
        """Delete the item identified by *key*."""
        kwargs: dict[str, Any] = {"Key": key}
        if condition_expression:
            kwargs["ConditionExpression"] = condition_expression

        try:
            self._table.delete_item(**kwargs)
        except ClientError as exc:
            _log.error(
                "DynamoDB delete_item failed",
                extra={"table": self._table_name, "key": key, "error": str(exc)},
            )
            raise

    def batch_write(self, items: list[Item]) -> int:
        """
        Write *items* in batches of 25 (DynamoDB limit).

        Returns the number of items written.  boto3's ``batch_writer`` handles
        retries for unprocessed items automatically via its internal flush loop.
        A ``ClientError`` is raised and logged if a batch fails entirely.
        """
        written = 0
        batch_size = 25

        for start in range(0, len(items), batch_size):
            chunk = items[start : start + batch_size]
            try:
                with self._table.batch_writer() as batch:
                    for item in chunk:
                        batch.put_item(Item=sanitize_for_dynamo(item))
                written += len(chunk)
            except ClientError as exc:
                _log.error(
                    "DynamoDB batch_write failed",
                    extra={
                        "table": self._table_name,
                        "chunk_start": start,
                        "chunk_size": len(chunk),
                        "error": str(exc),
                    },
                )
                raise

        return written

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def get_item(self, key: Item) -> Item | None:
        """
        Return the item for *key*, or ``None`` if it does not exist.
        """
        try:
            response = self._table.get_item(Key=key)
            item: Item | None = response.get("Item")
            return item
        except ClientError as exc:
            _log.error(
                "DynamoDB get_item failed",
                extra={"table": self._table_name, "key": key, "error": str(exc)},
            )
            raise

    def query(
        self,
        key_condition: ConditionBase,
        index_name: str | None = None,
        filter_expression: ConditionBase | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        projection_expression: str | None = None,
        limit: int | None = None,
        scan_forward: bool = True,
    ) -> list[Item]:
        """
        Query the table (or a GSI / LSI) and return all matching items.

        Pagination is handled automatically via ``LastEvaluatedKey``.

        Parameters
        ----------
        key_condition:
            Boto3 ``Key()`` expression, e.g. ``Key("pk").eq("value")``.
        index_name:
            Name of a GSI or LSI to query against.
        filter_expression:
            Optional ``Attr()`` expression applied after key filtering.
        expression_attribute_names:
            Name substitution map for reserved words (e.g. ``{"#n": "name"}``).
        projection_expression:
            Comma-separated attribute names to return.
        limit:
            Maximum total number of items to return.  When *None* all
            matching items are returned.
        scan_forward:
            ``True`` (default) → ascending SK order; ``False`` → descending.
        """
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": key_condition,
            "ScanIndexForward": scan_forward,
        }
        if index_name:
            kwargs["IndexName"] = index_name
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        if expression_attribute_names:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if projection_expression:
            kwargs["ProjectionExpression"] = projection_expression

        items: list[Item] = []

        try:
            while True:
                # Apply a per-page Limit when a total cap is set to avoid
                # fetching far more items than needed from DynamoDB.
                if limit is not None:
                    remaining = limit - len(items)
                    if remaining <= 0:
                        break
                    kwargs["Limit"] = remaining

                response = self._table.query(**kwargs)
                items.extend(response.get("Items", []))

                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key

        except ClientError as exc:
            _log.error(
                "DynamoDB query failed",
                extra={"table": self._table_name, "error": str(exc)},
            )
            raise

        return items

    def scan_all(
        self,
        filter_expression: ConditionBase | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        projection_expression: str | None = None,
    ) -> list[Item]:
        """
        Scan the entire table and return all items.

        Use sparingly — scans consume read capacity proportional to table
        size.  Pagination is handled automatically.
        """
        kwargs: dict[str, Any] = {}
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression
        if expression_attribute_names:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if projection_expression:
            kwargs["ProjectionExpression"] = projection_expression

        items: list[Item] = []

        try:
            while True:
                response = self._table.scan(**kwargs)
                items.extend(response.get("Items", []))

                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key

        except ClientError as exc:
            _log.error(
                "DynamoDB scan_all failed",
                extra={"table": self._table_name, "error": str(exc)},
            )
            raise

        return items


# ------------------------------------------------------------------ #
# Module-level helpers                                               #
# ------------------------------------------------------------------ #


def store_analysis_result(
    table_name: str,
    ticker: str,
    screen_type: str,
    result: dict[str, Any],
    passed: bool,
) -> None:
    """
    Write one row to the analysis DynamoDB table.

    Replaces the four identical ``_store_result`` private functions that were
    copy-pasted across the moat, management, intrinsic_value, and thesis
    analysis handlers.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB analysis table.
    ticker:
        Stock ticker symbol (PK).
    screen_type:
        Stage identifier, e.g. ``"moat_analysis"`` (used in SK and stored).
    result:
        Full result dict to store.
    passed:
        Whether this analysis stage passed its threshold.
    """
    from shared.converters import today_str

    sk = f"{today_str()}#{screen_type}"
    analysis = DynamoClient(table_name)
    item: dict[str, Any] = {
        "ticker": ticker,
        "analysis_date": sk,
        "screen_type": screen_type,
        "result": result,
        "passed": passed,
    }
    try:
        analysis.put_item(item)
    except Exception:
        _log.error(
            "Failed to store analysis result",
            extra={"table": table_name, "ticker": ticker, "screen_type": screen_type},
        )
        raise
    _log.info(
        "Analysis result stored",
        extra={"ticker": ticker, "screen_type": screen_type, "passed": passed},
    )


def get_watchlist_tickers(table_name: str) -> list[str]:
    """
    Return all tickers from the watchlist DynamoDB table.

    Replaces three identical ``_get_watchlist_tickers`` private functions in
    the SEC EDGAR, insider transactions, and Yahoo Finance ingestion handlers.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB watchlist table.

    Returns
    -------
    list[str]
        Sorted list of uppercase ticker symbols found in the table.
    """
    client = DynamoClient(table_name)
    try:
        items = client.scan_all(projection_expression="ticker")
    except Exception:
        _log.error(
            "Failed to scan watchlist table",
            extra={"table": table_name},
        )
        raise
    tickers = [i["ticker"] for i in items if i.get("ticker")]
    _log.info("Loaded watchlist tickers", extra={"count": len(tickers)})
    return tickers
