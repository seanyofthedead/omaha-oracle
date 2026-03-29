"""Thin Firecrawl wrapper with rate limiting, retry, and cost tracking.

Follows the same pattern as ``llm_client.py`` — a centralised client that
the rest of the codebase imports.  Uses ``ThreadPoolExecutor`` for concurrent
scraping (no asyncio, matching the codebase convention).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from firecrawl import FirecrawlApp

from shared.logger import get_logger

_log = get_logger(__name__)

# Default rate limiting: max concurrent requests and min delay between them.
_DEFAULT_MAX_CONCURRENT = 3
_DEFAULT_MIN_DELAY_S = 0.6  # ~100 req/min on Hobby plan


class FirecrawlClient:
    """Rate-limited, retry-aware Firecrawl client.

    Parameters
    ----------
    api_key:
        Firecrawl API key.
    max_concurrent:
        Maximum number of concurrent scrape requests.
    min_delay_s:
        Minimum seconds between successive requests (token-bucket style).
    max_retries:
        Maximum retries per request (SDK also retries on 502 internally).
    """

    def __init__(
        self,
        api_key: str,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        min_delay_s: float = _DEFAULT_MIN_DELAY_S,
        max_retries: int = 2,
    ) -> None:
        self._app = FirecrawlApp(api_key=api_key)
        self._semaphore = threading.Semaphore(max_concurrent)
        self._min_delay = min_delay_s
        self._max_retries = max_retries
        self._last_request_time = 0.0
        self._lock = threading.Lock()
        self._total_credits = 0
        self._credits_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Rate limiting                                                        #
    # ------------------------------------------------------------------ #

    def _throttle(self) -> None:
        """Enforce minimum delay between requests."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)
            self._last_request_time = time.monotonic()

    def _track_credit(self, count: int = 1) -> None:
        with self._credits_lock:
            self._total_credits += count

    @property
    def total_credits_used(self) -> int:
        """Total Firecrawl credits consumed by this client instance."""
        with self._credits_lock:
            return self._total_credits

    # ------------------------------------------------------------------ #
    # Scrape (single page)                                                 #
    # ------------------------------------------------------------------ #

    def scrape(
        self,
        url: str,
        formats: list[str | dict[str, Any]] | None = None,
        wait_for: int | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        """Scrape a single URL with rate limiting and retry.

        Returns a dict with ``url``, ``markdown``, ``metadata``, ``json``
        (when schema extraction is used), and ``scraped_at``.
        """
        kwargs: dict[str, Any] = {}
        if formats:
            kwargs["formats"] = formats
        if wait_for is not None:
            kwargs["timeout"] = wait_for
        if headers:
            kwargs["headers"] = headers
        if proxy:
            kwargs["proxy"] = proxy

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._semaphore.acquire()
            try:
                self._throttle()
                result = self._app.scrape(url, **kwargs)
                self._track_credit()
                return self._normalise_result(url, result)
            except Exception as exc:
                last_exc = exc
                _log.warning(
                    "Firecrawl scrape failed",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    },
                )
                if attempt < self._max_retries:
                    time.sleep(2 ** (attempt + 1))
            finally:
                self._semaphore.release()

        raise RuntimeError(
            f"Firecrawl scrape failed after {self._max_retries + 1} attempts: {url}"
        ) from last_exc

    # ------------------------------------------------------------------ #
    # Scrape with LLM extraction (structured data via Pydantic schema)     #
    # ------------------------------------------------------------------ #

    def scrape_extract(
        self,
        url: str,
        schema: dict[str, Any],
        prompt: str | None = None,
        wait_for: int | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        """Scrape a URL and extract structured data matching a JSON schema.

        Uses Firecrawl's inline JSON extraction format.
        """
        json_format: dict[str, Any] = {"type": "json", "schema": schema}
        if prompt:
            json_format["prompt"] = prompt

        formats: list[str | dict[str, Any]] = ["markdown", json_format]
        return self.scrape(url, formats=formats, wait_for=wait_for, headers=headers, proxy=proxy)

    # ------------------------------------------------------------------ #
    # Batch scrape (concurrent, ThreadPoolExecutor)                        #
    # ------------------------------------------------------------------ #

    def scrape_batch(
        self,
        urls: list[str],
        formats: list[str | dict[str, Any]] | None = None,
        wait_for: int | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        """Scrape multiple URLs concurrently using ThreadPoolExecutor.

        Failed URLs are logged and excluded from results (no exception raised).
        """
        workers = max_workers or min(len(urls), _DEFAULT_MAX_CONCURRENT)
        results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.scrape, url, formats, wait_for, headers, proxy): url
                for url in urls
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results.append(future.result())
                except Exception:
                    _log.warning("Batch scrape skipped failed URL", extra={"url": url})

        return results

    # ------------------------------------------------------------------ #
    # Normalise                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalise_result(url: str, raw: Any) -> dict[str, Any]:
        """Convert a Firecrawl Document response to a plain dict."""
        if isinstance(raw, dict):
            doc = raw
        elif hasattr(raw, "__dict__"):
            doc = {k: v for k, v in raw.__dict__.items() if not k.startswith("_")}
        else:
            doc = {"raw": raw}

        doc["url"] = url
        doc["scraped_at"] = time.time()
        return doc
