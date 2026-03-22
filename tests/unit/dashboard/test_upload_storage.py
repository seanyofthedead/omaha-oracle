"""Tests for S3 upload storage."""

from __future__ import annotations

import re

import boto3
import pytest
from moto import mock_aws

from dashboard.upload_storage import (
    check_duplicate_upload,
    extract_filing_text,
    store_uploaded_file,
)
from dashboard.upload_validator import UploadMetadata

S3_BUCKET = "omaha-oracle-dev-data"


@pytest.fixture()
def s3_bucket(aws_env: None):
    """Create an in-memory S3 bucket using moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=S3_BUCKET)
        yield s3


def _meta(**overrides) -> UploadMetadata:
    defaults = dict(
        ticker="AAPL",
        company_name="Apple Inc.",
        filing_type="10-K",
        fiscal_year=2025,
    )
    defaults.update(overrides)
    return UploadMetadata(**defaults)


class TestUploadStorage:
    def test_store_upload_writes_to_s3(self, s3_bucket):
        data = b"fake pdf content"
        key = store_uploaded_file(data, "report.pdf", _meta(), bucket=S3_BUCKET)
        obj = s3_bucket.get_object(Bucket=S3_BUCKET, Key=key)
        assert obj["Body"].read() == data

    def test_store_upload_key_format(self, s3_bucket):
        key = store_uploaded_file(b"data", "report.pdf", _meta(), bucket=S3_BUCKET)
        # Key should match: uploads/AAPL/2025/10-K/{timestamp}.pdf
        assert re.match(r"uploads/AAPL/2025/10-K/\d{8}T\d{6}\.pdf", key)

    def test_store_upload_returns_s3_key(self, s3_bucket):
        key = store_uploaded_file(b"data", "report.pdf", _meta(), bucket=S3_BUCKET)
        assert isinstance(key, str)
        assert key.startswith("uploads/")


# ---------------------------------------------------------------------------
# Filing context extraction
# ---------------------------------------------------------------------------


class TestFilingContextExtraction:
    def test_html_extracts_text(self):
        html = b"<html><body><h1>Revenue</h1><p>$1B in sales</p></body></html>"
        text = extract_filing_text(html, "filing.html", _meta())
        assert "Revenue" in text
        assert "$1B in sales" in text
        # HTML tags should be stripped
        assert "<h1>" not in text

    def test_pdf_returns_placeholder(self):
        text = extract_filing_text(b"%PDF-1.4 binary content", "report.pdf", _meta())
        assert "PDF" in text or "pdf" in text

    def test_xlsx_returns_placeholder(self):
        text = extract_filing_text(b"PK\x03\x04 fake xlsx", "data.xlsx", _meta())
        assert "spreadsheet" in text.lower() or "xlsx" in text.lower()

    def test_text_truncated_to_limit(self):
        html = b"<p>" + b"A" * 20_000 + b"</p>"
        text = extract_filing_text(html, "big.html", _meta(), max_chars=100)
        assert len(text) <= 200  # prefix + truncated content + suffix
        assert "truncated" in text.lower()

    def test_pdf_extraction_returns_warning_marker(self):
        """PDF extraction must return text containing 'not yet supported'."""
        text = extract_filing_text(b"%PDF-1.4 binary content", "report.pdf", _meta())
        assert "not yet supported" in text.lower()

    def test_xlsx_extraction_returns_warning_marker(self):
        """XLSX extraction must return text containing 'not yet supported'."""
        text = extract_filing_text(b"PK\x03\x04 fake xlsx", "data.xlsx", _meta())
        assert "not yet supported" in text.lower()

    def test_context_prefixed_with_metadata(self):
        text = extract_filing_text(b"<p>data</p>", "filing.html", _meta())
        assert "10-K" in text
        assert "Apple Inc." in text
        assert "AAPL" in text


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_detects_existing_upload(self, s3_bucket):
        meta = _meta()
        store_uploaded_file(b"data", "report.pdf", meta, bucket=S3_BUCKET)
        assert check_duplicate_upload(meta, bucket=S3_BUCKET) is True

    def test_no_dup_different_year(self, s3_bucket):
        store_uploaded_file(b"data", "report.pdf", _meta(fiscal_year=2024), bucket=S3_BUCKET)
        assert check_duplicate_upload(_meta(fiscal_year=2025), bucket=S3_BUCKET) is False

    def test_no_dup_different_type(self, s3_bucket):
        store_uploaded_file(b"data", "report.pdf", _meta(filing_type="10-K"), bucket=S3_BUCKET)
        assert check_duplicate_upload(_meta(filing_type="10-Q"), bucket=S3_BUCKET) is False

    def test_no_dup_empty_bucket(self, s3_bucket):
        assert check_duplicate_upload(_meta(), bucket=S3_BUCKET) is False
