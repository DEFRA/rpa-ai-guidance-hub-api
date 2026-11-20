"""The search service: search in one guide, where it says where the words are in the
version of the guide it has indexed (`GET /v1/documents/{documentId}/matches`), and
indexing, where it is given each version saved
(`PUT /documents/{documentId}/versions/{versionId}`)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

import httpx2

from app.common import http_client
from app.guidance import ids
from app.search import guide_search

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.guidance import models


class IndexRefusedError(Exception):
    """The search service refused a version (4xx): sending it again would not help."""


class SearchServiceGuideSearch:
    def __init__(
        self,
        base_url: str,
        open_client: Callable[[], httpx2.AsyncClient] = http_client.create_async_client,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._open_client = open_client

    async def in_guide(
        self, guide_id: ids.DocumentId, words: str
    ) -> guide_search.GuideMatches | None:
        """Where the words are in the version the search service has indexed; None
        when it has no version of the guide indexed, which it cannot tell apart from
        no guide."""
        async with self._open_client() as client:
            response = await client.get(
                f"{self._base_url}/v1/documents/{guide_id}/matches",
                params={"q": words},
            )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return _guide_matches(response.json())


def _guide_matches(body: dict[str, Any]) -> guide_search.GuideMatches:
    return guide_search.GuideMatches(
        version_id=ids.VersionId(uuid.UUID(body["versionId"])),
        sections=tuple(
            guide_search.SectionMatches(
                anchor=section["anchor"],
                matches=tuple(
                    guide_search.Match(match["start"], match["end"], match["text"])
                    for match in section["matches"]
                ),
            )
            for section in body["sections"]
        ),
    )


class SearchServiceIndex:
    def __init__(
        self,
        base_url: str,
        open_client: Callable[[], httpx2.AsyncClient] = http_client.create_async_client,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._open_client = open_client

    async def index(self, version: models.Version, metadata: Mapping[str, Any]) -> None:
        """Give the search service a saved version, to index as its guide's latest.

        It answers before it has indexed it, so a failure there is not seen here.

        Raises:
            IndexRefusedError: if it refuses the version (4xx).
            httpx2.HTTPStatusError: if it fails (5xx), so that it can be tried again.
        """
        async with self._open_client() as client:
            response = await client.put(
                f"{self._base_url}/documents/{version.document_id}/versions/{version.id}",
                json=_index_request(version, metadata),
            )
        if response.is_client_error:
            message = (
                f"The search service refused version {version.id}: "
                f"{response.status_code} {response.text}"
            )
            raise IndexRefusedError(message)
        response.raise_for_status()


def _index_request(
    version: models.Version, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """The version as the search service indexes it. Its title is the version's, else
    the journey's, as the guide's page shows it. Mongo keeps the time in UTC without
    its zone, which the search service requires."""
    saved_at = version.created_at.replace(tzinfo=version.created_at.tzinfo or dt.UTC)
    return {
        "sourceType": "guide",
        "status": "published",
        "occurredAt": saved_at.isoformat(),
        "versionNumber": version.version_number,
        "contentUrl": version.content_url,
        "metadata": {
            **metadata,
            "guideTitle": version.title or metadata.get("guideTitle"),
        },
    }
