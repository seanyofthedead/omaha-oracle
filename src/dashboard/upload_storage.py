"""S3 storage for uploaded financial statements."""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser

import boto3
from botocore.config import Config as BotocoreConfig

from dashboard.upload_validator import UploadMetadata, get_file_extension
from shared.config import get_config
from shared.logger import get_logger

_S3_CONFIG = BotocoreConfig(retries={"mode": "adaptive", "max_attempts": 5})
_log = get_logger(__name__)


def store_uploaded_file(
    file_bytes: bytes,
    filename: str,
    metadata: UploadMetadata,
    *,
    bucket: str | None = None,
) -> str:
    """Upload *file_bytes* to S3 and return the object key.

    Key format: ``uploads/{ticker}/{year}/{filing_type}/{timestamp}.{ext}``
    """
    cfg = get_config()
    bucket = bucket or cfg.s3_bucket
    ext = get_file_extension(filename)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    key = f"{metadata.s3_upload_prefix}{ts}.{ext}"

    content_types = {
        "pdf": "application/pdf",
        "html": "text/html",
        "htm": "text/html",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
    }

    client = boto3.client("s3", region_name=cfg.aws_region, config=_S3_CONFIG)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=file_bytes,
        ContentType=content_types.get(ext, "application/octet-stream"),
    )
    _log.info("Uploaded file to S3", extra={"bucket": bucket, "key": key, "bytes": len(file_bytes)})
    return key


# ---------------------------------------------------------------------------
# Filing context extraction
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML tag stripper using stdlib only."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def extract_filing_text(
    file_bytes: bytes,
    filename: str,
    metadata: UploadMetadata,
    *,
    max_chars: int = 10_000,
) -> str:
    """Extract readable text from an uploaded filing for LLM context.

    Returns a metadata-prefixed string suitable for injection into analysis
    prompts. HTML is tag-stripped; PDF and XLSX return placeholders (full
    parsing is a future enhancement).
    """
    ext = get_file_extension(filename)
    prefix = (
        f"Uploaded filing: {metadata.filing_type} for "
        f"{metadata.company_name} ({metadata.ticker}), FY{metadata.fiscal_year}\n\n"
    )

    if ext in ("html", "htm"):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(file_bytes.decode("utf-8", errors="replace"))
        except Exception:
            return prefix + "Could not parse HTML filing."
        body = parser.get_text()
    elif ext == "pdf":
        body = "PDF filing uploaded. Full text extraction is not yet supported."
    elif ext in ("xlsx", "xls"):
        body = "Spreadsheet (XLSX) filing uploaded. Full parsing is not yet supported."
    else:
        body = "Filing uploaded in unsupported format."

    if len(body) > max_chars:
        body = body[:max_chars] + "\n...[truncated]"

    return prefix + body


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def check_duplicate_upload(
    metadata: UploadMetadata,
    *,
    bucket: str | None = None,
) -> bool:
    """Return True if a file has already been uploaded for this ticker/type/year."""
    cfg = get_config()
    bucket = bucket or cfg.s3_bucket
    prefix = metadata.s3_upload_prefix

    client = boto3.client("s3", region_name=cfg.aws_region, config=_S3_CONFIG)
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return response.get("KeyCount", 0) > 0
