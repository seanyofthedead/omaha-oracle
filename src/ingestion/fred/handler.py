"""
Lambda handler for FRED macro data ingestion.

Fetches 10 macro series from the FRED API and stores in S3:
  processed/macro/{metric_name}.json
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from shared.config import get_config
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
OBSERVATIONS_PER_SERIES = 120  # ~10 years monthly

# 10 macro series from FRED
MACRO_SERIES = [
    "FEDFUNDS",      # Federal funds rate
    "DGS10",         # 10-year Treasury
    "DGS2",          # 2-year Treasury
    "T10Y2Y",        # 10y-2y spread
    "CPIAUCSL",      # CPI all urban
    "UNRATE",        # Unemployment rate
    "VIXCLS",        # VIX closing
    "BAMLH0A0HYM2",  # High yield spread
    "GDP",           # GDP
    "UMCSENT",       # U Michigan consumer sentiment
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
    resp = requests.get(FRED_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    observations = data.get("observations", [])
    return observations


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Fetches all 10 macro series and stores each to S3.
    """
    cfg = get_config()
    api_key = cfg.get_fred_key()
    s3 = S3Client()
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    stored = 0
    errors: list[str] = []

    for series_id in MACRO_SERIES:
        try:
            observations = _fetch_series(series_id, api_key)
            payload = {
                "series_id": series_id,
                "fetched_at": date_str,
                "count": len(observations),
                "observations": observations,
            }
            s3.write_json(f"processed/macro/{series_id}.json", payload)
            stored += 1
            _log.info(
                "Stored FRED series",
                extra={"series_id": series_id, "observations": len(observations)},
            )
        except Exception as exc:
            errors.append(f"{series_id}: {exc}")
            _log.exception("Failed to fetch FRED series", extra={"series_id": series_id})

    return {
        "status": "ok" if not errors else "partial",
        "stored": stored,
        "total": len(MACRO_SERIES),
        "errors": errors[:10],
    }
