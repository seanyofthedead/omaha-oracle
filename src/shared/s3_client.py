"""
S3 JSON and Markdown helpers for Omaha Oracle.

All methods operate on a single bucket (defaulting to the one configured in
``Settings.s3_bucket``) and raise ``ClientError`` after structured logging on
any AWS failure.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.config import get_config
from shared.logger import get_logger

_log = get_logger(__name__)


class S3Client:
    """
    High-level S3 helper focused on JSON and Markdown objects.

    Parameters
    ----------
    bucket:
        Bucket name to use.  When *None* falls back to
        ``get_config().s3_bucket``.
    """

    def __init__(self, bucket: str | None = None) -> None:
        cfg = get_config()
        self._bucket = bucket or cfg.s3_bucket
        self._client = boto3.client("s3", region_name=cfg.aws_region)

    # ------------------------------------------------------------------ #
    # JSON                                                                 #
    # ------------------------------------------------------------------ #

    def write_json(
        self,
        key: str,
        data: Any,
        indent: int | None = 2,
    ) -> None:
        """
        Serialise *data* and upload it to *key* with ``application/json``.

        Parameters
        ----------
        key:
            S3 object key (path within the bucket).
        data:
            Any JSON-serialisable Python object.
        indent:
            Pretty-print indentation level; pass ``None`` for compact output.
        """
        body = json.dumps(data, indent=indent, default=str).encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                ContentEncoding="utf-8",
            )
            _log.debug(
                "S3 JSON written",
                extra={"bucket": self._bucket, "key": key, "bytes": len(body)},
            )
        except ClientError as exc:
            _log.error(
                "S3 write_json failed",
                extra={"bucket": self._bucket, "key": key, "error": str(exc)},
            )
            raise

    def read_json(self, key: str) -> Any:
        """
        Download *key* and deserialise it as JSON.

        Returns
        -------
        Any
            The deserialised Python object (typically ``dict`` or ``list``).

        Raises
        ------
        ClientError
            When the object does not exist or the caller lacks permission.
        json.JSONDecodeError
            When the stored bytes are not valid JSON.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            raw = response["Body"].read()
            data: Any = json.loads(raw)
            _log.debug(
                "S3 JSON read",
                extra={"bucket": self._bucket, "key": key, "bytes": len(raw)},
            )
            return data
        except ClientError as exc:
            _log.error(
                "S3 read_json failed",
                extra={"bucket": self._bucket, "key": key, "error": str(exc)},
            )
            raise

    # ------------------------------------------------------------------ #
    # Markdown                                                             #
    # ------------------------------------------------------------------ #

    def write_markdown(self, key: str, text: str) -> None:
        """
        Upload *text* to *key* with ``text/markdown; charset=utf-8``.

        Parameters
        ----------
        key:
            S3 object key.
        text:
            Markdown string to store.
        """
        body = text.encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="text/markdown; charset=utf-8",
            )
            _log.debug(
                "S3 Markdown written",
                extra={"bucket": self._bucket, "key": key, "bytes": len(body)},
            )
        except ClientError as exc:
            _log.error(
                "S3 write_markdown failed",
                extra={"bucket": self._bucket, "key": key, "error": str(exc)},
            )
            raise

    def read_markdown(self, key: str) -> str:
        """
        Download *key* and return its content as a UTF-8 string.

        Raises
        ------
        ClientError
            When the object does not exist or the caller lacks permission.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            raw = response["Body"].read()
            text = raw.decode("utf-8")
            _log.debug(
                "S3 Markdown read",
                extra={"bucket": self._bucket, "key": key, "bytes": len(raw)},
            )
            return text
        except ClientError as exc:
            _log.error(
                "S3 read_markdown failed",
                extra={"bucket": self._bucket, "key": key, "error": str(exc)},
            )
            raise

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    def list_keys(self, prefix: str = "") -> list[str]:
        """
        Return all object keys under *prefix* in the bucket.

        Uses ``list_objects_v2`` with automatic pagination so that buckets
        with more than 1 000 objects are handled correctly.

        Parameters
        ----------
        prefix:
            Key prefix to filter by (e.g. ``"analysis/AAPL/"``).  Pass an
            empty string (default) to list every key in the bucket.

        Returns
        -------
        list[str]
            Sorted list of matching S3 object keys.
        """
        keys: list[str] = []
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}

        try:
            while True:
                response = self._client.list_objects_v2(**kwargs)
                for obj in response.get("Contents", []):
                    keys.append(obj["Key"])

                if response.get("IsTruncated"):
                    kwargs["ContinuationToken"] = response["NextContinuationToken"]
                else:
                    break

        except ClientError as exc:
            _log.error(
                "S3 list_keys failed",
                extra={"bucket": self._bucket, "prefix": prefix, "error": str(exc)},
            )
            raise

        return sorted(keys)
