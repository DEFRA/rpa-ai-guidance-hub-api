"""Search in one guide, done by the search service: it says where the words are in
the version of the guide it has indexed (`GET /v1/documents/{documentId}/matches`)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx2

from app.common import http_client
from app.guidance import ids
from app.search import guide_search

if TYPE_CHECKING:
    from collections.abc import Callable


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
