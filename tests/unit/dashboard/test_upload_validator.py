"""Tests for file upload validation and metadata model."""

from __future__ import annotations

import io

import pytest
from pydantic import ValidationError

from dashboard.upload_validator import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_BYTES,
    UploadMetadata,
    get_file_extension,
    validate_upload,
)


def _make_file(name: str, size: int = 1024) -> io.BytesIO:
    """Create a BytesIO object that mimics a Streamlit UploadedFile."""
    buf = io.BytesIO(b"x" * size)
    buf.name = name
    buf.size = size
    return buf


# ---------------------------------------------------------------------------
# File validator
# ---------------------------------------------------------------------------


class TestFileValidator:
    def test_accepts_pdf(self):
        errors = validate_upload(_make_file("report.pdf"))
        assert errors == []

    def test_accepts_xlsx(self):
        errors = validate_upload(_make_file("financials.xlsx"))
        assert errors == []

    def test_accepts_xls(self):
        errors = validate_upload(_make_file("financials.xls"))
        assert errors == []

    def test_accepts_html(self):
        errors = validate_upload(_make_file("filing.html"))
        assert errors == []

    def test_accepts_htm(self):
        errors = validate_upload(_make_file("filing.htm"))
        assert errors == []

    def test_rejects_unsupported_type(self):
        errors = validate_upload(_make_file("report.docx"))
        assert len(errors) == 1
        assert "Unsupported file type" in errors[0]

    def test_rejects_oversized_file(self):
        big = MAX_FILE_SIZE_BYTES + 1
        errors = validate_upload(_make_file("report.pdf", size=big))
        assert len(errors) == 1
        assert "50" in errors[0] or "size" in errors[0].lower()

    def test_rejects_empty_file(self):
        errors = validate_upload(_make_file("report.pdf", size=0))
        assert len(errors) == 1
        assert "empty" in errors[0].lower()

    def test_multiple_errors_reported(self):
        """An empty .docx should produce two errors: type + empty."""
        errors = validate_upload(_make_file("report.docx", size=0))
        assert len(errors) == 2

    def test_extracts_extension_simple(self):
        assert get_file_extension("report.pdf") == "pdf"

    def test_extracts_extension_compound(self):
        assert get_file_extension("report.10-K.pdf") == "pdf"

    def test_extracts_extension_uppercase(self):
        assert get_file_extension("REPORT.PDF") == "pdf"

    def test_allowed_extensions_is_a_set(self):
        assert isinstance(ALLOWED_EXTENSIONS, (set, frozenset))


# ---------------------------------------------------------------------------
# Upload metadata model
# ---------------------------------------------------------------------------


class TestUploadMetadata:
    def test_valid_metadata(self):
        m = UploadMetadata(
            ticker="AAPL",
            company_name="Apple Inc.",
            filing_type="10-K",
            fiscal_year=2025,
        )
        assert m.ticker == "AAPL"
        assert m.company_name == "Apple Inc."

    def test_ticker_normalized_to_uppercase(self):
        m = UploadMetadata(
            ticker="aapl",
            company_name="Apple Inc.",
            filing_type="10-K",
            fiscal_year=2025,
        )
        assert m.ticker == "AAPL"

    def test_ticker_stripped(self):
        m = UploadMetadata(
            ticker="  AAPL  ",
            company_name="Apple Inc.",
            filing_type="10-K",
            fiscal_year=2025,
        )
        assert m.ticker == "AAPL"

    def test_empty_ticker_rejected(self):
        with pytest.raises(ValidationError, match="ticker"):
            UploadMetadata(
                ticker="",
                company_name="Apple Inc.",
                filing_type="10-K",
                fiscal_year=2025,
            )

    def test_whitespace_only_ticker_rejected(self):
        with pytest.raises(ValidationError, match="ticker"):
            UploadMetadata(
                ticker="   ",
                company_name="Apple Inc.",
                filing_type="10-K",
                fiscal_year=2025,
            )

    def test_company_name_required(self):
        with pytest.raises(ValidationError, match="company_name"):
            UploadMetadata(
                ticker="AAPL",
                company_name="",
                filing_type="10-K",
                fiscal_year=2025,
            )

    def test_filing_type_valid_enum(self):
        for ft in ("10-K", "10-Q", "Annual Report", "Quarterly Report"):
            m = UploadMetadata(
                ticker="AAPL",
                company_name="Apple Inc.",
                filing_type=ft,
                fiscal_year=2025,
            )
            assert m.filing_type == ft

    def test_filing_type_invalid_rejected(self):
        with pytest.raises(ValidationError, match="filing_type"):
            UploadMetadata(
                ticker="AAPL",
                company_name="Apple Inc.",
                filing_type="INVALID",
                fiscal_year=2025,
            )

    def test_fiscal_year_too_old_rejected(self):
        with pytest.raises(ValidationError, match="fiscal_year"):
            UploadMetadata(
                ticker="AAPL",
                company_name="Apple Inc.",
                filing_type="10-K",
                fiscal_year=1800,
            )

    def test_fiscal_year_too_future_rejected(self):
        with pytest.raises(ValidationError, match="fiscal_year"):
            UploadMetadata(
                ticker="AAPL",
                company_name="Apple Inc.",
                filing_type="10-K",
                fiscal_year=2100,
            )

    def test_s3_upload_prefix_property(self):
        m = UploadMetadata(
            ticker="AAPL",
            company_name="Apple Inc.",
            filing_type="10-K",
            fiscal_year=2025,
        )
        assert m.s3_upload_prefix == "uploads/AAPL/2025/10-K/"
