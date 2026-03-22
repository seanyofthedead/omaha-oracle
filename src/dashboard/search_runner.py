"""Search engine: pipeline execution and search loop for company search."""

from __future__ import annotations

import sys as _sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import analysis.intrinsic_value.handler as _iv_mod
import analysis.management_quality.handler as _mgmt_mod
import analysis.moat_analysis.handler as _moat_mod
import analysis.thesis_generator.handler as _thesis_mod
from analysis.quant_screen.screener import screen_company
from dashboard.candidate_generator import (
    CandidateGenerator,
    ingest_ticker_data,
    pre_screen_ticker,
)
from dashboard.search_config import (
    MAX_EVALUATIONS,
    SearchConfig,
    check_quality_gates,
    count_gates_passed,
)
from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

# Expose handler references so tests can patch them at module level.
moat_handler = _moat_mod.handler
mgmt_handler = _mgmt_mod.handler
iv_handler = _iv_mod.handler
thesis_handler = _thesis_mod.handler

_THIS = _sys.modules[__name__]


# ---------------------------------------------------------------------------
# C1: SearchResult
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """Result of evaluating a single company through the pipeline."""

    ticker: str
    company_name: str
    moat_score: int
    management_score: int
    margin_of_safety: float
    intrinsic_value: float | None
    current_price: float | None
    passed_all_gates: bool
    gate_details: dict[str, bool]
    gates_passed_count: int
    error: str | None
    raw_result: dict


# ---------------------------------------------------------------------------
# C2: SearchProgress
# ---------------------------------------------------------------------------


@dataclass
class SearchProgress:
    """Progress state for a running search."""

    evaluated_count: int
    match_count: int
    elapsed_seconds: float
    time_limit_seconds: int
    current_ticker: str
    current_action: str
    results: list[SearchResult]
    is_complete: bool
    was_cancelled: bool
    candidates_exhausted: bool


# ---------------------------------------------------------------------------
# C3: _run_pipeline_stages
# ---------------------------------------------------------------------------

_ANALYSIS_STAGES = [
    ("moat_analysis", "moat_handler"),
    ("management_quality", "mgmt_handler"),
    ("intrinsic_value", "iv_handler"),
]


def _run_pipeline_stages(
    ticker: str,
    company_data: dict[str, Any],
    financials_client: DynamoClient,
) -> dict[str, Any]:
    """Run quant screen + stages 2-4 (moat, management, IV).

    Does NOT run thesis (stage 5) — that's handled separately for qualifiers.
    """
    # Stage 1: Quant screen
    thresholds = {}  # Use defaults from screener
    try:
        quant_result, quant_passed = screen_company(
            ticker, company_data, financials_client, thresholds
        )
    except Exception as exc:
        _log.warning("Quant screen failed", extra={"ticker": ticker, "error": str(exc)})
        return {
            **company_data,
            "quant_passed": False,
            "error": str(exc),
            "failed_stage": "quant_screen",
        }

    if not quant_passed:
        return {**company_data, **quant_result, "quant_passed": False}

    # Stages 2-4: moat, management, IV
    current: dict[str, Any] = {**company_data, **quant_result, "quant_passed": True}

    for stage_name, attr_name in _ANALYSIS_STAGES:
        handler_fn = getattr(_THIS, attr_name)
        try:
            current = handler_fn(current, None)
        except Exception as exc:
            _log.error(
                "Pipeline stage failed",
                extra={"stage": stage_name, "ticker": ticker, "error": str(exc)},
            )
            current["failed_stage"] = stage_name
            current["error"] = str(exc)
            break

    return current


# ---------------------------------------------------------------------------
# C4: _run_thesis_for_qualifiers
# ---------------------------------------------------------------------------


def _run_thesis_for_qualifiers(
    qualifiers: list[SearchResult],
) -> list[SearchResult]:
    """Run thesis generation (stage 5) for companies that passed all gates."""
    updated: list[SearchResult] = []
    for sr in qualifiers:
        try:
            thesis_result = thesis_handler(sr.raw_result, None)
            sr.raw_result = thesis_result
        except Exception as exc:
            _log.warning(
                "Thesis generation failed",
                extra={"ticker": sr.ticker, "error": str(exc)},
            )
            sr.error = f"Thesis generation failed: {exc}"
        updated.append(sr)
    return updated


# ---------------------------------------------------------------------------
# C5: run_search
# ---------------------------------------------------------------------------


def _build_search_result(
    ticker: str,
    pipeline_result: dict[str, Any],
) -> SearchResult:
    """Build a SearchResult from a pipeline output dict."""
    passed, gate_details = check_quality_gates(pipeline_result)
    return SearchResult(
        ticker=ticker,
        company_name=pipeline_result.get("company_name", ticker),
        moat_score=pipeline_result.get("moat_score", 0),
        management_score=pipeline_result.get("management_score", 0),
        margin_of_safety=pipeline_result.get("margin_of_safety", 0.0),
        intrinsic_value=pipeline_result.get("intrinsic_value_per_share"),
        current_price=pipeline_result.get("current_price"),
        passed_all_gates=passed,
        gate_details=gate_details,
        gates_passed_count=count_gates_passed(gate_details),
        error=pipeline_result.get("error"),
        raw_result=pipeline_result,
    )


def run_search(
    config: SearchConfig,
    progress: dict[str, Any],
    cancel_event: threading.Event,
) -> list[SearchResult]:
    """Run a company search, returning all evaluated results.

    Updates ``progress`` dict in-place for UI polling.
    Stops when: match_count >= num_results, time limit, hard cap, cancel, or exhaustion.
    """
    cfg = get_config()
    financials_client = DynamoClient(cfg.table_financials)
    generator = CandidateGenerator(seed=int(time.time()))

    results: list[SearchResult] = []
    evaluated_count = 0
    match_count = 0
    time_limit_seconds = config.time_limit_minutes * 60
    start_time = time.monotonic()

    while True:
        # Check stop conditions
        if cancel_event.is_set():
            break
        if match_count >= config.num_results:
            break
        elapsed = time.monotonic() - start_time
        if elapsed >= time_limit_seconds:
            break
        if evaluated_count >= MAX_EVALUATIONS:
            break

        # Get next batch
        batch = generator.generate_batch(10)
        if not batch:
            progress["candidates_exhausted"] = True
            break

        for ticker in batch:
            # Re-check stop conditions
            if cancel_event.is_set():
                break
            if match_count >= config.num_results:
                break
            elapsed = time.monotonic() - start_time
            if elapsed >= time_limit_seconds:
                break
            if evaluated_count >= MAX_EVALUATIONS:
                break

            # Update progress
            progress.update({
                "current_ticker": ticker,
                "current_action": f"Pre-screening {ticker}",
                "evaluated_count": evaluated_count,
                "match_count": match_count,
                "elapsed_seconds": elapsed,
            })

            # Pre-screen
            passed_prescreen, info = pre_screen_ticker(ticker)
            if not passed_prescreen:
                continue

            # Ingest data
            progress["current_action"] = f"Ingesting data for {ticker}"
            cik = generator.get_cik(ticker)
            if cik and not ingest_ticker_data(ticker, cik):
                continue

            # Run pipeline
            progress["current_action"] = f"Running pipeline for {ticker}"
            try:
                pipeline_result = _run_pipeline_stages(ticker, info, financials_client)
            except Exception as exc:
                _log.error("Pipeline error", extra={"ticker": ticker, "error": str(exc)})
                sr = SearchResult(
                    ticker=ticker,
                    company_name=info.get("company_name", ticker),
                    moat_score=0,
                    management_score=0,
                    margin_of_safety=0.0,
                    intrinsic_value=None,
                    current_price=None,
                    passed_all_gates=False,
                    gate_details={"moat": False, "management": False, "mos": False},
                    gates_passed_count=0,
                    error=str(exc),
                    raw_result={},
                )
                results.append(sr)
                evaluated_count += 1
                continue

            evaluated_count += 1
            sr = _build_search_result(ticker, pipeline_result)
            results.append(sr)

            if sr.passed_all_gates:
                match_count += 1

            # Update progress with results
            progress.update({
                "evaluated_count": evaluated_count,
                "match_count": match_count,
                "results": results,
            })

    # Run thesis for qualifiers
    qualifiers = [r for r in results if r.passed_all_gates]
    if qualifiers and not cancel_event.is_set():
        progress["current_action"] = "Generating investment theses..."
        qualifiers = _run_thesis_for_qualifiers(qualifiers)
        # Update results list with thesis-enriched qualifiers
        qualifier_tickers = {q.ticker for q in qualifiers}
        results = [
            next(q for q in qualifiers if q.ticker == r.ticker)
            if r.ticker in qualifier_tickers
            else r
            for r in results
        ]

    progress.update({
        "is_complete": True,
        "was_cancelled": cancel_event.is_set(),
        "results": results,
        "evaluated_count": evaluated_count,
        "match_count": match_count,
        "elapsed_seconds": time.monotonic() - start_time,
    })

    return results
