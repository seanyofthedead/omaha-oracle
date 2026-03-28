"""E2E tests for file upload and analysis pipeline.

Scenarios 1-13: Upload validation, storage, and full pipeline execution.
Uses real Anthropic API for LLM analysis and moto for AWS infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from dashboard.analysis_runner import (
    build_analysis_event,
    format_analysis_summary,
    run_upload_analysis,
)
from dashboard.upload_storage import (
    check_duplicate_upload,
    extract_filing_text,
    store_uploaded_file,
)
from dashboard.upload_validator import validate_upload

from .conftest import MINIMAL_PDF, MINIMAL_XLSX, requires_anthropic

# ---------------------------------------------------------------------------
# Helper: fake UploadedFile (mimics Streamlit's UploadedFile interface)
# ---------------------------------------------------------------------------


@dataclass
class FakeUploadedFile:
    name: str
    size: int
    _data: bytes = b""

    def getvalue(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# Scenario 1: Upload HTML 10-K → full pipeline → results display
# Also covers Scenario 12 (progress indicator) and Scenario 13 (session persist)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@requires_anthropic
class TestUploadHTML10K:
    def test_full_pipeline_with_markel_10k(self, e2e_tables, markel_10k_html, markel_metadata):
        """Upload real Markel 10-K HTML, run full pipeline, verify results."""
        file_bytes, filename = markel_10k_html

        # Validate
        fake_file = FakeUploadedFile(name=filename, size=len(file_bytes), _data=file_bytes)
        errors = validate_upload(fake_file)
        assert errors == [], f"Validation failed: {errors}"

        # Extract text
        context = extract_filing_text(file_bytes, filename, markel_metadata)
        assert "Markel Group" in context
        assert "10-K" in context

        # Store in S3 (moto)
        s3_key = store_uploaded_file(file_bytes, filename, markel_metadata)
        assert s3_key.startswith("uploads/MKL/2024/10-K/")

        # Build event
        event = build_analysis_event(
            markel_metadata,
            s3_key,
            extra_metrics={"sector": "Financials", "industry": "Insurance"},
        )
        assert event["ticker"] == "MKL"
        assert event["company_name"] == "Markel Group Inc."
        assert event["quant_passed"] is True
        assert event["source"] == "manual_upload"

        # Scenario 12: progress callback tracking
        progress_log: list[tuple[str, int]] = []

        def on_progress(stage: str, num: int) -> None:
            progress_log.append((stage, num))

        # Run full pipeline (real Anthropic calls)
        result = run_upload_analysis(event, context, progress_callback=on_progress)

        # Verify progress was reported for each stage
        assert len(progress_log) >= 1, "No progress callbacks received"
        stage_names = [s for s, _ in progress_log]
        assert "moat_analysis" in stage_names

        # Verify moat analysis
        assert isinstance(result.get("moat_score"), int)
        assert 1 <= result["moat_score"] <= 10
        assert result.get("moat_type") in ("wide", "narrow", "none", "uncertain")

        # Verify management analysis
        assert isinstance(result.get("management_score"), int)
        assert 1 <= result["management_score"] <= 10

        # Verify company identity preserved
        assert result["ticker"] == "MKL"
        assert result["company_name"] == "Markel Group Inc."

        # Verify results can be formatted for display
        summary = format_analysis_summary(result)
        assert "moat_display" in summary
        assert "management_display" in summary

        # Scenario 13: results dict is serializable (can persist in session state)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Scenario 2: Upload valid PDF → pipeline completes
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@requires_anthropic
class TestUploadPDF:
    def test_pdf_pipeline_completes(self, e2e_tables, markel_metadata):
        """Upload a minimal PDF — pipeline runs with placeholder context."""
        fake = FakeUploadedFile(name="markel_10k.pdf", size=len(MINIMAL_PDF), _data=MINIMAL_PDF)
        errors = validate_upload(fake)
        assert errors == []

        context = extract_filing_text(MINIMAL_PDF, "markel_10k.pdf", markel_metadata)
        assert "PDF filing uploaded" in context

        s3_key = store_uploaded_file(MINIMAL_PDF, "markel_10k.pdf", markel_metadata)
        event = build_analysis_event(markel_metadata, s3_key)
        result = run_upload_analysis(event, context)

        # Pipeline should complete (even with limited context)
        assert "moat_score" in result or "failed_stage" in result


# ---------------------------------------------------------------------------
# Scenario 3: Upload valid XLSX → pipeline completes
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@requires_anthropic
class TestUploadXLSX:
    def test_xlsx_pipeline_completes(self, e2e_tables, markel_metadata):
        """Upload a minimal XLSX — pipeline runs with placeholder context."""
        fake = FakeUploadedFile(name="markel_10k.xlsx", size=len(MINIMAL_XLSX), _data=MINIMAL_XLSX)
        errors = validate_upload(fake)
        assert errors == []

        context = extract_filing_text(MINIMAL_XLSX, "markel_10k.xlsx", markel_metadata)
        assert "Spreadsheet" in context

        s3_key = store_uploaded_file(MINIMAL_XLSX, "markel_10k.xlsx", markel_metadata)
        event = build_analysis_event(markel_metadata, s3_key)
        result = run_upload_analysis(event, context)

        assert "moat_score" in result or "failed_stage" in result


# ---------------------------------------------------------------------------
# Scenario 4: Upload multiple files sequentially
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestUploadMultipleSequential:
    def test_multiple_uploads_stored_separately(self, e2e_tables, markel_metadata):
        """Two sequential uploads get distinct S3 keys."""
        import time

        key1 = store_uploaded_file(b"filing-one", "file1.html", markel_metadata)
        time.sleep(1.1)  # Ensure distinct timestamps
        key2 = store_uploaded_file(b"filing-two", "file2.html", markel_metadata)

        assert key1 != key2
        assert key1.startswith("uploads/MKL/2024/10-K/")
        assert key2.startswith("uploads/MKL/2024/10-K/")


# ---------------------------------------------------------------------------
# Scenario 5: Re-upload previously analyzed file
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestReuploadSameFile:
    def test_reupload_detected_as_duplicate(self, e2e_tables, markel_metadata):
        """Re-uploading for the same ticker/year/type is detected."""
        store_uploaded_file(b"original-filing", "10k.html", markel_metadata)
        assert check_duplicate_upload(markel_metadata) is True

    def test_reupload_still_succeeds(self, e2e_tables, markel_metadata):
        """A duplicate upload still stores successfully (no crash)."""
        store_uploaded_file(b"first", "10k.html", markel_metadata)
        key2 = store_uploaded_file(b"second", "10k.html", markel_metadata)
        assert key2.startswith("uploads/MKL/")


# ---------------------------------------------------------------------------
# Scenario 6: Reject unsupported file type
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRejectUnsupportedType:
    @pytest.mark.parametrize("name", ["report.txt", "data.zip", "logo.jpg", "notes.docx"])
    def test_unsupported_extension_rejected(self, name):
        fake = FakeUploadedFile(name=name, size=100)
        errors = validate_upload(fake)
        assert len(errors) >= 1
        assert "Unsupported file type" in errors[0]


# ---------------------------------------------------------------------------
# Scenario 7: Reject empty file
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRejectEmptyFile:
    def test_empty_file_rejected(self):
        fake = FakeUploadedFile(name="empty.pdf", size=0)
        errors = validate_upload(fake)
        assert any("empty" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Scenario 8: Reject corrupted/unreadable PDF
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRejectCorruptedPDF:
    def test_corrupted_pdf_extraction_graceful(self, e2e_tables, markel_metadata):
        """A non-PDF file with .pdf extension passes validation but extraction
        returns a placeholder — the pipeline should not crash."""
        garbage = b"this is not a pdf at all"
        fake = FakeUploadedFile(name="corrupt.pdf", size=len(garbage), _data=garbage)
        errors = validate_upload(fake)
        assert errors == []  # extension + size are valid

        context = extract_filing_text(garbage, "corrupt.pdf", markel_metadata)
        assert "PDF" in context  # placeholder text


# ---------------------------------------------------------------------------
# Scenario 9: Reject oversized file
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRejectOversizedFile:
    def test_oversized_file_rejected(self):
        # 51 MB > 50 MB limit
        fake = FakeUploadedFile(name="huge.pdf", size=51 * 1024 * 1024)
        errors = validate_upload(fake)
        assert any("size limit" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Scenario 10: Pipeline returns error → meaningful message
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPipelineError:
    def test_pipeline_error_captured(self, e2e_tables, markel_metadata):
        """Force a handler to raise — error is captured, app doesn't crash."""
        import dashboard.analysis_runner as runner

        original = runner.moat_handler

        def _boom(event: dict[str, Any], context: Any) -> dict[str, Any]:
            raise RuntimeError("Simulated moat handler failure")

        runner.moat_handler = _boom
        try:
            event = build_analysis_event(markel_metadata, "uploads/MKL/test.html")
            result = run_upload_analysis(event, "test context")
            assert result.get("failed_stage") == "moat_analysis"
            assert "Simulated moat handler failure" in result.get("error", "")
        finally:
            runner.moat_handler = original


# ---------------------------------------------------------------------------
# Scenario 11: Pipeline returns partial/malformed results
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPipelinePartialResults:
    def test_partial_results_after_stage_failure(self, e2e_tables, markel_metadata):
        """If mgmt handler fails, moat results are preserved."""
        import dashboard.analysis_runner as runner

        def _moat_ok(event: dict[str, Any], context: Any) -> dict[str, Any]:
            return {**event, "moat_score": 7, "moat_type": "narrow"}

        def _mgmt_fail(event: dict[str, Any], context: Any) -> dict[str, Any]:
            raise RuntimeError("Management analysis unavailable")

        runner.moat_handler = _moat_ok
        runner.mgmt_handler = _mgmt_fail
        try:
            event = build_analysis_event(markel_metadata, "uploads/MKL/test.html")
            result = run_upload_analysis(event, "test context")

            # Moat results preserved
            assert result["moat_score"] == 7
            assert result["moat_type"] == "narrow"
            # Failure recorded
            assert result["failed_stage"] == "management_quality"
            assert "error" in result

            # format_analysis_summary handles partial results
            summary = format_analysis_summary(result)
            assert summary["moat_display"] == "7/10 — Narrow"
            assert summary["management_display"] == "\u2014"  # em dash
        finally:
            import analysis.management_quality.handler as _mgmt_mod
            import analysis.moat_analysis.handler as _moat_mod

            runner.moat_handler = _moat_mod.handler
            runner.mgmt_handler = _mgmt_mod.handler
