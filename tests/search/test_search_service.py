"""Search in one guide, and indexing, by the search service, against a stub of its
API.

All fixture text is invented.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx2
import pytest

from app.guidance import ids, models
from app.search import guide_search, search_service, title_search
from tests.fixtures import search_service as answers

BASE_URL = "http://search.test"


def _searching(handler) -> search_service.SearchServiceGuideSearch:
    return search_service.SearchServiceGuideSearch(
        BASE_URL,
        open_client=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def _indexing(handler) -> search_service.SearchServiceIndex:
    return search_service.SearchServiceIndex(
        BASE_URL,
        open_client=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def _version(title: str | None = "Paying claims") -> models.Version:
    """A version as read back from Mongo, which keeps its time in UTC with no zone."""
    document_id = ids.new_document_id()
    version_id = ids.new_version_id()
    return models.Version(
        id=version_id,
        document_id=document_id,
        content_url=f"s3://managed-docs/{document_id}/{version_id}/content.md",
        created_at=dt.datetime(2026, 11, 20, 13, 44),  # noqa: DTZ001 - as Mongo gives it
        title=title,
        version_number="1.4",
    )


def _accepting(asked: list[httpx2.Request]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        return httpx2.Response(202, json=answers.accepted("document-id", "version-id"))

    return handler


class TestIndexingAVersionInTheSearchService:
    async def test_sends_the_version_where_its_content_is_as_published(self):
        version = _version()
        asked: list[httpx2.Request] = []

        await _indexing(_accepting(asked)).index(
            version, {"guideTitle": "Paying claims", "schemes": ["Scheme A"]}
        )

        assert asked[0].method == "PUT"
        assert asked[0].url.path == (
            f"/documents/{version.document_id}/versions/{version.id}"
        )
        assert json.loads(asked[0].content) == {
            "sourceType": "guide",
            "status": "published",
            "occurredAt": "2026-11-20T13:44:00+00:00",
            "versionNumber": "1.4",
            "contentUrl": version.content_url,
            "metadata": {"guideTitle": "Paying claims", "schemes": ["Scheme A"]},
        }

    async def test_names_the_guide_by_its_version_title_as_the_page_does(self):
        asked: list[httpx2.Request] = []

        await _indexing(_accepting(asked)).index(
            _version(title="Paying claims 2026"), {"guideTitle": "Paying claims"}
        )

        assert json.loads(asked[0].content)["metadata"]["guideTitle"] == (
            "Paying claims 2026"
        )

    async def test_names_the_guide_by_the_journeys_title_when_the_version_has_none(
        self,
    ):
        asked: list[httpx2.Request] = []

        await _indexing(_accepting(asked)).index(
            _version(title=""), {"guideTitle": "Paying claims"}
        )

        assert json.loads(asked[0].content)["metadata"]["guideTitle"] == (
            "Paying claims"
        )

    async def test_is_refused_for_good_when_the_search_service_refuses_the_version(
        self,
    ):
        indexing = _indexing(lambda _request: httpx2.Response(422))
        version = _version()

        with pytest.raises(search_service.IndexRefusedError):
            await indexing.index(version, {"guideTitle": "Paying claims"})

    async def test_fails_to_be_tried_again_when_the_search_service_fails(self):
        indexing = _indexing(lambda _request: httpx2.Response(503))
        version = _version()

        with pytest.raises(search_service.SearchServiceError) as raised:
            await indexing.index(version, {"guideTitle": "Paying claims"})

        assert raised.type is search_service.SearchServiceError

    async def test_fails_to_be_tried_again_when_the_search_service_does_not_answer(
        self,
    ):
        def refusing_to_connect(request: httpx2.Request) -> httpx2.Response:
            message = "connection refused"
            raise httpx2.ConnectError(message, request=request)

        indexing = _indexing(refusing_to_connect)
        version = _version()

        with pytest.raises(search_service.SearchServiceError) as raised:
            await indexing.index(version, {"guideTitle": "Paying claims"})

        assert raised.type is search_service.SearchServiceError

    async def test_keeps_a_refusal_short(self):
        indexing = _indexing(lambda _request: httpx2.Response(422, text="x" * 1000))
        version = _version()

        with pytest.raises(search_service.IndexRefusedError) as raised:
            await indexing.index(version, {"guideTitle": "Paying claims"})

        assert len(str(raised.value)) < 300  # noqa: PLR2004 - not the 1000 sent


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

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(503, id="an error"),
            pytest.param(302, id="a redirect, which the client does not follow"),
        ],
    )
    async def test_says_when_the_search_service_does_not_succeed(self, status):
        searching = _searching(lambda _request: httpx2.Response(status))
        guide_id = ids.new_document_id()

        with pytest.raises(search_service.SearchServiceError, match=str(status)):
            await searching.in_guide(guide_id, "pay")

    async def test_says_when_the_search_service_does_not_answer(self):
        searching = _searching(_refused)
        guide_id = ids.new_document_id()

        with pytest.raises(search_service.SearchServiceError, match="did not answer"):
            await searching.in_guide(guide_id, "pay")


def _finding(handler) -> search_service.SearchServiceTitleSearch:
    return search_service.SearchServiceTitleSearch(
        BASE_URL,
        open_client=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


class TestFindingGuidesByTitleInTheSearchService:
    async def test_gives_the_guides_it_finds_the_likest_first(self):
        meant = ids.new_document_id()
        other = ids.new_document_id()
        asked: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            asked.append(request)
            return httpx2.Response(
                200,
                json=answers.title_search(
                    answers.title_result(str(meant), "Breaches and Reductions", 0.9),
                    answers.title_result(str(other), "Reductions in Grants", 0.6),
                ),
            )

        found = await _finding(handler).like("reducions & breaches", limit=8)

        assert (asked[0].url.path, dict(asked[0].url.params)) == (
            "/v1/search",
            {"title": "reducions & breaches", "limit": "8"},
        )
        assert found == (
            title_search.GuideTitle(meant, "Breaches and Reductions"),
            title_search.GuideTitle(other, "Reductions in Grants"),
        )

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(503, id="an error"),
            pytest.param(302, id="a redirect, which the client does not follow"),
        ],
    )
    async def test_says_when_the_search_service_does_not_succeed(self, status):
        finding = _finding(lambda _request: httpx2.Response(status))

        with pytest.raises(search_service.SearchServiceError, match=str(status)):
            await finding.like("reductions", limit=8)

    async def test_says_when_the_search_service_does_not_answer(self):
        finding = _finding(_refused)

        with pytest.raises(search_service.SearchServiceError, match="did not answer"):
            await finding.like("reductions", limit=8)


def _refused(request: httpx2.Request) -> httpx2.Response:
    message = "Connection refused"
    raise httpx2.ConnectError(message, request=request)
