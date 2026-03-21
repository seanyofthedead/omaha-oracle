"""
Shared HTTP session with retry and timeout configuration.

Usage
-----
    from shared.http_client import get_session, TIMEOUT

    resp = get_session().get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()

    # POST (e.g. Alpaca order) — use bare requests.post, NOT the session:
    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 5s TCP connect, 30s response read
TIMEOUT: tuple[int, int] = (5, 30)

_RETRY_CONFIG = Retry(
    total=3,
    backoff_factor=1.0,  # delays: 0s, 1s, 2s
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),  # POST intentionally excluded
    raise_on_status=False,  # let callers call resp.raise_for_status()
    respect_retry_after_header=True,  # honour FRED/Alpaca Retry-After headers
)

_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY_CONFIG))
_SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY_CONFIG))


def get_session() -> requests.Session:
    """
    Return the shared HTTP session with retry adapter pre-mounted.

    The module-level singleton reuses urllib3 connection pools across Lambda
    warm-start invocations (same benefit as boto3 client reuse).

    Do NOT use this session for POST requests to Alpaca — pass bare
    ``requests.post()`` instead to prevent duplicate order submission.
    """
    return _SESSION
