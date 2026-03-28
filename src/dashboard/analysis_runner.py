"""In-process analysis pipeline runner for uploaded financial statements.

Calls the analysis handlers (moat, management, IV, thesis) sequentially,
injecting uploaded filing context instead of fetching from S3.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Callable
from typing import Any

import analysis.intrinsic_value.handler as _iv_mod
import analysis.management_quality.handler as _mgmt_mod
import analysis.moat_analysis.handler as _moat_mod
import analysis.thesis_generator.handler as _thesis_mod
from dashboard.upload_validator import UploadMetadata
from shared.logger import get_logger

_log = get_logger(__name__)

# Expose handler references as module attributes so tests can patch them.
moat_handler = _moat_mod.handler
mgmt_handler = _mgmt_mod.handler
iv_handler = _iv_mod.handler
thesis_handler = _thesis_mod.handler


def build_analysis_event(
    metadata: UploadMetadata,
    upload_s3_key: str,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the event dict expected by analysis pipeline handlers.

    Parameters
    ----------
    metadata:
        Validated upload metadata.
    upload_s3_key:
        S3 key where the uploaded file is stored.
    extra_metrics:
        Optional dict of additional metrics (sector, industry, price, etc.)
        to merge into the event's ``metrics`` field.
    """
    metrics: dict[str, Any] = {"sector": "Unknown", "industry": "Unknown"}
    if extra_metrics:
        metrics.update(extra_metrics)

    return {
        "ticker": metadata.ticker,
        "company_name": metadata.company_name,
        "metrics": metrics,
        "quant_passed": True,
        "source": "manual_upload",
        "upload_s3_key": upload_s3_key,
        "filing_type": metadata.filing_type,
        "fiscal_year": metadata.fiscal_year,
    }


_STAGE_NAMES = [
    ("moat_analysis", "moat_handler"),
    ("management_quality", "mgmt_handler"),
    ("intrinsic_value", "iv_handler"),
    ("thesis_generator", "thesis_handler"),
]

# Module reference for lazy lookup — allows tests to patch handler attrs.
_THIS = _sys.modules[__name__]


def _default_get_filing_context() -> Callable[..., tuple[str, bool]]:
    """Return the real S3Client.get_filing_context method."""
    from shared.s3_client import S3Client

    client = S3Client()
    fn: Callable[..., tuple[str, bool]] = client.get_filing_context
    return fn


def _make_filing_context_override(
    filing_context: str,
) -> Callable[..., tuple[str, bool]]:
    """Return a callable that always returns *filing_context*."""

    def _override(*_args: Any, **_kwargs: Any) -> tuple[str, bool]:
        return (filing_context, False)

    return _override


def run_upload_analysis(
    event: dict[str, Any],
    filing_context: str,
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    get_filing_context: Callable[..., tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """Run analysis stages 2-5 in sequence, returning the accumulated result.

    Filing context from the uploaded document is injected via the
    *get_filing_context* callable so existing handlers see it without
    modification.

    Parameters
    ----------
    event:
        The base event dict (from ``build_analysis_event``).
    filing_context:
        Extracted text from the uploaded filing.
    progress_callback:
        Optional ``(stage_name, stage_number) -> None`` called before each stage.
    get_filing_context:
        Optional callable replacing ``S3Client.get_filing_context``.  Defaults
        to a closure that returns *filing_context* directly.
    """
    if get_filing_context is None:
        get_filing_context = _make_filing_context_override(filing_context)

    current = dict(event)
    current["_get_filing_context"] = get_filing_context

    for idx, (stage_name, attr_name) in enumerate(_STAGE_NAMES, start=1):
        if progress_callback:
            progress_callback(stage_name, idx)

        handler_fn = getattr(_THIS, attr_name)

        try:
            current = handler_fn(current, None)
        except Exception as exc:
            _log.error(
                "Pipeline stage failed",
                extra={"stage": stage_name, "error": str(exc)},
            )
            current["failed_stage"] = stage_name
            current["error"] = str(exc)
            break

    return current


# ---------------------------------------------------------------------------
# Results formatting
# ---------------------------------------------------------------------------

_EM_DASH = "\u2014"


def format_analysis_summary(result: dict[str, Any]) -> dict[str, str]:
    """Transform raw pipeline output into display-ready strings.

    Returns a dict with keys: ``moat_display``, ``management_display``,
    ``iv_display``, ``mos_display``, ``buy_signal``.
    """
    skipped = result.get("skipped", False)
    failed = result.get("failed_stage")

    # Moat
    moat_score = result.get("moat_score", 0)
    moat_type = result.get("moat_type", "")
    if skipped and moat_score == 0:
        moat_display = "Skipped (budget exhausted)"
    elif failed and moat_score == 0:
        moat_display = _EM_DASH
    elif moat_score == 0:
        moat_display = _EM_DASH
    else:
        moat_display = f"{moat_score}/10 — {moat_type.title()}"

    # Management
    mgmt_score = result.get("management_score", 0)
    if mgmt_score == 0:
        mgmt_display = _EM_DASH
    else:
        mgmt_display = f"{mgmt_score}/10"

    # Intrinsic value
    iv = result.get("intrinsic_value_per_share")
    if iv:
        iv_display = f"${iv:,.2f}"
    else:
        iv_display = _EM_DASH

    # Margin of safety
    mos = result.get("margin_of_safety")
    if mos is not None and mos != 0:
        mos_display = f"{mos * 100:.1f}%"
    else:
        mos_display = _EM_DASH

    # Buy signal
    buy = result.get("thesis_generated", False)
    buy_signal = "BUY" if buy else _EM_DASH

    return {
        "moat_display": moat_display,
        "management_display": mgmt_display,
        "iv_display": iv_display,
        "mos_display": mos_display,
        "buy_signal": buy_signal,
    }
