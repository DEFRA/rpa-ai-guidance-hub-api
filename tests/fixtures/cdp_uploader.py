"""cdp-uploader callback fixture factories (§1.3).

Contract based on cdp-uploader webhook payload structure deposited on completed
file upload and scan.
"""

from __future__ import annotations

from typing import Any

EXAMPLE_FILE_ID = "9fcaabe5-77ec-44db-8356-3a6e8dc51b13"
EXAMPLE_S3_KEY = (
    "scanned/3b0b2a02-a669-44ba-9b78-bd5cb8460253/9fcaabe5-77ec-44db-8356-3a6e8dc51b13"
)


def uploaded_file_entry(
    file_id: str = EXAMPLE_FILE_ID,
    *,
    file_status: str = "complete",
    s3_key: str = EXAMPLE_S3_KEY,
    filename: str = "photo.jpeg",
    content_type: str = "image/jpeg",
    content_length: int = 11264,
    s3_bucket: str = "cdp-example-node-frontend",
    **overrides: Any,
) -> dict[str, Any]:
    """A single uploaded file dict inside cdp-uploader form data."""
    entry = {
        "fileId": file_id,
        "filename": filename,
        "contentType": content_type,
        "detectedContentType": content_type,
        "contentLength": content_length,
        "checksumSha256": "bng5jOVC6TxEgwTUlX4DikFtDEYEc8vQTsOP0ZAv21c=",
        "fileStatus": file_status,
        "s3Key": s3_key,
        "s3Bucket": s3_bucket,
    }
    entry.update(overrides)
    return entry


def cdp_callback_payload(
    file_entries: list[dict[str, Any]] | dict[str, Any] | None = None,
    *,
    upload_status: str = "ready",
    customer_id: str = "1234",
    field_name: str = "file",
    extra_form_fields: dict[str, Any] | None = None,
    number_of_rejected_files: int = 0,
    **overrides: Any,
) -> dict[str, Any]:
    """A full cdp-uploader callback request payload."""
    form: dict[str, Any] = {"button": "upload"}
    if extra_form_fields:
        form.update(extra_form_fields)

    if file_entries is not None:
        form[field_name] = file_entries
    else:
        form[field_name] = uploaded_file_entry()

    payload = {
        "uploadStatus": upload_status,
        "metadata": {"customerId": customer_id},
        "form": form,
        "numberOfRejectedFiles": number_of_rejected_files,
    }

    payload.update(overrides)
    return payload
