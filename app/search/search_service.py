"""Searches done by the search service, and its index. Search in one guide: it says
where the words are in the version of the guide it has indexed
(`GET /v1/documents/{documentId}/matches`). Finding guides by title: it gives the
guides whose titles are like the one given (`GET /v1/search?title=`). Indexing: it is
given each version saved (`PUT /documents/{documentId}/versions/{versionId}`).

When the search service does not answer, or answers with an error, each raises
`SearchServiceError`; indexing raises `IndexRefusedError`, one of them, when it
refuses a version.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

import httpx2

from app.common import http_client
from app.guidance import ids
from app.search import guide_search, title_search

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.guidance import models


# How much of a refusal's text is kept: enough to say why, not the whole request
# echoed back.
_REFUSAL_TEXT_LIMIT = 200


class SearchServiceError(Exception):
    """The search service did not answer, or answered with an error."""


class IndexRefusedError(SearchServiceError):
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
        response = await _asked(
            self._open_client,
            "GET",
            f"{self._base_url}/v1/documents/{guide_id}/matches",
            params={"q": words},
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
            "GET",
            f"{self._base_url}/v1/search",
            params={"title": title, "limit": limit},
        )
        return tuple(
            title_search.GuideTitle(
                ids.DocumentId(uuid.UUID(result["documentId"])), result["documentTitle"]
            )
            for result in _successful(response).json()["results"]
        )


async def _asked(
    open_client: Callable[[], httpx2.AsyncClient],
    method: str,
    url: str,
    **request: Any,
) -> httpx2.Response:
    """The search service's answer, whatever its status."""
    try:
        async with open_client() as client:
            return await client.request(method, url, **request)
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
            SearchServiceError: if it does not answer, or fails (5xx), so that it can
                be tried again.
        """
        response = await _asked(
            self._open_client,
            "PUT",
            f"{self._base_url}/documents/{version.document_id}/versions/{version.id}",
            json=_index_request(version, metadata),
        )
        if response.is_client_error:
            message = (
                f"The search service refused version {version.id}: "
                f"{response.status_code} {response.text[:_REFUSAL_TEXT_LIMIT]}"
            )
            raise IndexRefusedError(message)
        _successful(response)


def _index_request(
    version: models.Version, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """The version as the search service indexes it. Its title is the version's, else
    the journey's, as the guide's page shows it. A detail with no value is left out:
    the search service refuses a list that is null, and reads one that is absent as
    empty. Mongo keeps the time in UTC without its zone, which the search service
    requires."""
    saved_at = version.created_at.replace(tzinfo=version.created_at.tzinfo or dt.UTC)
    given = {name: value for name, value in metadata.items() if value is not None}
    return {
        "sourceType": "guide",
        "status": "published",
        "occurredAt": saved_at.isoformat(),
        "versionNumber": version.version_number,
        "contentUrl": version.content_url,
        "metadata": {
            **given,
            "guideTitle": version.title or metadata.get("guideTitle"),
        },
    }
