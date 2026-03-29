"""
Lambda handler for FRED macro data ingestion.

Fetches 10 macro series from the FRED API and stores in S3:
  processed/macro/{metric_name}.json
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from shared.config import get_config
from shared.converters import check_failure_threshold, today_str
from shared.http_client import TIMEOUT, get_session
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
OBSERVATIONS_PER_SERIES = 120  # ~10 years monthly

# 10 macro series from FRED
MACRO_SERIES = [
    "FEDFUNDS",  # Federal funds rate
    "DGS10",  # 10-year Treasury
    "DGS2",  # 2-year Treasury
    "T10Y2Y",  # 10y-2y spread
    "CPIAUCSL",  # CPI all urban
    "UNRATE",  # Unemployment rate
    "VIXCLS",  # VIX closing
    "BAMLH0A0HYM2",  # High yield spread
    "GDP",  # GDP
    "UMCSENT",  # U Michigan consumer sentiment
]


def _fetch_series(series_id: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch up to 120 observations for a FRED series."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": OBSERVATIONS_PER_SERIES,
        "sort_order": "desc",  # most recent first
    }
    resp = get_session().get(FRED_BASE, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    observations: list[dict[str, Any]] = data.get("observations", [])
    return observations


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Fetches all 10 macro series and stores each to S3.
    """
    cfg = get_config()
    api_key = cfg.get_fred_key()
    s3 = S3Client()
    date_str = today_str()

    stored = 0
    errors: list[str] = []

    def _fetch_and_store(series_id: str) -> str | None:
        """Fetch and store one series. Returns series_id on success, None on failure."""
        observations = _fetch_series(series_id, api_key)
        payload = {
            "series_id": series_id,
            "fetched_at": date_str,
            "count": len(observations),
            "observations": observations,
        }
        s3.write_json(f"processed/macro/{series_id}.json", payload)
        _log.info(
            "Stored FRED series",
            extra={"series_id": series_id, "observations": len(observations)},
        )
        return series_id

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(_fetch_and_store, sid): sid for sid in MACRO_SERIES}
        for fut in as_completed(futures):
            sid = futures[fut]
            exc = fut.exception()
            if exc:
                errors.append(f"{sid}: {exc}")
                _log.exception(
                    "Ingestion failed",
                    extra={
                        "series_id": sid,
                        "stage": "fetch_series",
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                stored += 1

    check_failure_threshold(errors, len(MACRO_SERIES), "FRED ingestion")

    return {
        "status": "ok" if not errors else "partial",
        "stored": stored,
        "total": len(MACRO_SERIES),
        "errors": errors[:10],
    }
