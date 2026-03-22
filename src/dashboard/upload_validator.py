"""File upload validation and metadata model for the Upload Analysis page."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "xlsx", "xls", "html", "htm"})
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


def get_file_extension(filename: str) -> str:
    """Return the lowercase file extension (without dot) from *filename*."""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot + 1 :].lower()


def validate_upload(file: Any) -> list[str]:
    """Validate an uploaded file, returning a list of error strings (empty = valid).

    Parameters
    ----------
    file:
        A file-like object with ``name`` and ``size`` attributes
        (e.g. a Streamlit ``UploadedFile``).
    """
    errors: list[str] = []

    ext = get_file_extension(file.name)
    if ext not in ALLOWED_EXTENSIONS:
        errors.append(
            f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )

    if file.size == 0:
        errors.append("File is empty. Please upload a non-empty file.")
    elif file.size > MAX_FILE_SIZE_BYTES:
        limit_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        errors.append(f"File exceeds {limit_mb} MB size limit.")

    return errors


class UploadMetadata(BaseModel):
    """Validated metadata for an uploaded financial statement."""

    ticker: str
    company_name: str
    filing_type: Literal["10-K", "10-Q", "Annual Report", "Quarterly Report"]
    fiscal_year: int

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("Ticker must not be empty.")
        return v

    @field_validator("company_name")
    @classmethod
    def _require_company_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Company name must not be empty.")
        return v.strip()

    @field_validator("fiscal_year")
    @classmethod
    def _validate_fiscal_year(cls, v: int) -> int:
        current_year = datetime.now(UTC).year
        if v < 1900 or v > current_year + 2:
            raise ValueError(f"Fiscal year must be between 1900 and {current_year + 2}.")
        return v

    @property
    def s3_upload_prefix(self) -> str:
        """S3 key prefix for this upload: ``uploads/{ticker}/{year}/{type}/``."""
        return f"uploads/{self.ticker}/{self.fiscal_year}/{self.filing_type}/"
