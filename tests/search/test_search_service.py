"""Search in one guide by the search service, against a stub of its API.

All fixture text is invented.
"""

from __future__ import annotations

import httpx2
import pytest

from app.guidance import ids
from app.search import guide_search, search_service
from tests.fixtures import search_service as answers

BASE_URL = "http://search.test"


def _searching(handler) -> search_service.SearchServiceGuideSearch:
    return search_service.SearchServiceGuideSearch(
        BASE_URL,
        open_client=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


class TestSearchingAGuideInTheSearchService:
    async def test_gives_where_the_search_service_finds_the_words(self):
        guide_id = ids.new_document_id()
        version_id = ids.new_version_id()
        asked: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            asked.append(request)
            return httpx2.Response(
                200,
                json=answers.document_matches(
                    str(version_id),
                    answers.section(None, answers.match(0, 3, "pay")),
                    answers.section("1-paying", answers.match(2, 5, "Pay")),
                ),
            )

        found = await _searching(handler).in_guide(guide_id, "pay")

        assert asked[0].url.path == f"/v1/documents/{guide_id}/matches"
        assert found == guide_search.GuideMatches(
            version_id,
            (
                guide_search.SectionMatches(None, (guide_search.Match(0, 3, "pay"),)),
                guide_search.SectionMatches(
                    "1-paying", (guide_search.Match(2, 5, "Pay"),)
                ),
            ),
        )

    async def test_sends_the_words_as_they_are(self):
        words = "land & use #2"
        asked: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            asked.append(request)
            return httpx2.Response(
                200, json=answers.document_matches(str(ids.new_version_id()))
            )

        await _searching(handler).in_guide(ids.new_document_id(), words)

        assert asked[0].url.params["q"] == words

    async def test_finds_no_guide_the_search_service_has_not_indexed(self):
        searching = _searching(lambda _request: httpx2.Response(404))

        found = await searching.in_guide(ids.new_document_id(), "pay")

        assert found is None

    async def test_says_when_the_search_service_fails(self):
        searching = _searching(lambda _request: httpx2.Response(503))
        guide_id = ids.new_document_id()

        with pytest.raises(httpx2.HTTPStatusError):
            await searching.in_guide(guide_id, "pay")
