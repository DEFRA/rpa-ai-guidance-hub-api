"""The search service's answers, as factories.

Checked against `DocumentMatchesResponse` in the search service's
`app/search/v1/schemas.py` and `docs/openapi.json`, at `eba1583`, and `Accepted` in
its `app/ingestion/schemas.py` and `docs/openapi.json`, at `f4201e1`: camelCase on the
wire.
"""

from __future__ import annotations

from typing import Any


def match(start: int, end: int, text: str) -> dict[str, Any]:
    """One place the words are in a section's text, as `Match`."""
    return {"start": start, "end": end, "text": text}


def section(anchor: str | None, *matches: dict[str, Any]) -> dict[str, Any]:
    """A section's matches, as `SectionMatches`; the introduction has no anchor."""
    return {"anchor": anchor, "matches": list(matches)}


def document_matches(version_id: str, *sections: dict[str, Any]) -> dict[str, Any]:
    """`GET /v1/documents/{documentId}/matches`, as `DocumentMatchesResponse`."""
    return {"versionId": version_id, "sections": list(sections)}


def accepted(document_id: str, version_id: str) -> dict[str, Any]:
    """`PUT /documents/{documentId}/versions/{versionId}`, as `Accepted`."""
    return {
        "documentId": document_id,
        "versionId": version_id,
        "status": "published",
        "operation": "index",
    }
