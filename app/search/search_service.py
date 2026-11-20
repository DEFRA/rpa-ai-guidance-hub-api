"""Searches done by the search service. Search in one guide: it says where the words
are in the version of the guide it has indexed (`GET /v1/documents/{documentId}/matches`).
Finding guides by title: it gives the guides whose titles are like the one given
(`GET /v1/search?title=`).

When the search service does not answer, or answers with an error, each raises
`SearchServiceError`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx2

from app.common import http_client
from app.guidance import ids
from app.search import guide_search, title_search

if TYPE_CHECKING:
    from collections.abc import Callable


class SearchServiceError(Exception):
    """The search service did not answer, or answered with an error."""


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
        response = await _asked(
            self._open_client,
            f"{self._base_url}/v1/documents/{guide_id}/matches",
            {"q": words},
        )
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        return _guide_matches(_successful(response).json())


class SearchServiceTitleSearch:
    def __init__(
        self,
        base_url: str,
        open_client: Callable[[], httpx2.AsyncClient] = http_client.create_async_client,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._open_client = open_client

    async def like(self, title: str, limit: int) -> tuple[title_search.GuideTitle, ...]:
        """The guides whose titles the search service finds like `title`, the likest
        first."""
        response = await _asked(
            self._open_client,
            f"{self._base_url}/v1/search",
            {"title": title, "limit": limit},
        )
        return tuple(
            title_search.GuideTitle(
                ids.DocumentId(uuid.UUID(result["documentId"])), result["documentTitle"]
            )
            for result in _successful(response).json()["results"]
        )


async def _asked(
    open_client: Callable[[], httpx2.AsyncClient], url: str, params: dict[str, Any]
) -> httpx2.Response:
    """The search service's answer, whatever its status."""
    try:
        async with open_client() as client:
            return await client.get(url, params=params)
    except httpx2.HTTPError as error:
        message = f"The search service did not answer: {error}"
        raise SearchServiceError(message) from error


def _successful(response: httpx2.Response) -> httpx2.Response:
    if not response.is_success:
        message = f"The search service answered {response.status_code}"
        raise SearchServiceError(message)
    return response


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
