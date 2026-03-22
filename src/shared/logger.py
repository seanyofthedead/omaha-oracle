"""
Structured JSON logging for Omaha Oracle.

Every log record is emitted as a single-line JSON object:

    {
        "timestamp": "2026-03-15T12:00:00.123456+00:00",
        "level": "INFO",
        "logger": "src.shared.config",
        "message": "Settings loaded",
        "correlation_id": "a1b2c3d4-...",
        "function": "get_settings"
    }

Works identically in Lambda (CloudWatch ingests each stdout line as one
log event) and locally (same stdout stream).

Usage
-----
    from src.shared.logger import get_logger, set_correlation_id

    set_correlation_id("req-abc123")
    log = get_logger(__name__)
    log.info("Hello", extra={"ticker": "AAPL"})
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# ------------------------------------------------------------------ #
# Correlation-ID context variable                                      #
# ------------------------------------------------------------------ #

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(value: str) -> None:
    """Attach *value* as the correlation ID for the current async context."""
    _correlation_id.set(value)


def get_correlation_id() -> str:
    """Return the correlation ID for the current async context (empty string if unset)."""
    return _correlation_id.get()


# ------------------------------------------------------------------ #
# JSON formatter                                                       #
# ------------------------------------------------------------------ #


class JsonFormatter(logging.Formatter):
    """
    Emit each log record as a compact JSON line.

    The standard fields are always present:
        timestamp, level, logger, message, correlation_id, function

    Any ``extra`` key-value pairs passed to the logger call are merged in
    at the top level, making it easy to attach structured metadata:

        log.info("order placed", extra={"ticker": "AAPL", "qty": 10})
    """

    # Keys that live on LogRecord but must not be re-emitted as extra fields
    _RESERVED: frozenset[str] = frozenset(
        logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        ).__dict__.keys()
        | {"message", "asctime"}
    )

    def format(self, record: logging.LogRecord) -> str:
        """Serialize *record* to a JSON string with standard fields."""
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "correlation_id": get_correlation_id(),
            "function": record.funcName,
        }

        # Merge caller-supplied extra fields (skip internal LogRecord attrs)
        for key, val in record.__dict__.items():
            if key not in self._RESERVED:
                payload[key] = val

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


# ------------------------------------------------------------------ #
# Logger factory                                                       #
# ------------------------------------------------------------------ #


def _build_handler() -> logging.StreamHandler[Any]:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    return handler


def get_logger(name: str, level: int | str | None = None) -> logging.Logger:
    """
    Return a logger that emits structured JSON to stdout.

    Calling this multiple times with the same *name* returns the same
    ``logging.Logger`` instance (standard library guarantee), but the
    JSON handler is only attached once.

    Parameters
    ----------
    name:
        Logger name — use ``__name__`` in module code.
    level:
        Optional override for this specific logger.  When *None* the
        level is inherited from the root logger (or the ``LOG_LEVEL``
        env var applied at root during Lambda cold-start bootstrap).
    """
    logger = logging.getLogger(name)

    # Attach our JSON handler exactly once per logger instance
    if not any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
        for h in logger.handlers
    ):
        logger.addHandler(_build_handler())
        logger.propagate = False  # avoid double-printing to the root handler

    if level is not None:
        logger.setLevel(level)

    return logger


# ------------------------------------------------------------------ #
# Root-logger bootstrap (called once at import time)                   #
# ------------------------------------------------------------------ #


def _bootstrap_root_logger() -> None:
    """
    Configure the root logger with a JSON handler so that third-party
    libraries that use ``logging.getLogger()`` also emit structured JSON.

    Lambda sets the root logger level via the ``LOG_LEVEL`` environment
    variable automatically; we honour whatever level is already set.
    """
    root = logging.getLogger()
    if not any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
        for h in root.handlers
    ):
        root.addHandler(_build_handler())
        # Only set a default level when nothing has been configured yet
        if root.level == logging.NOTSET:
            root.setLevel(logging.INFO)


_bootstrap_root_logger()
