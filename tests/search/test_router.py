"""Searching one guide, and finding guides by title, over HTTP, against a real MongoDB;
the guide's content is stubbed.

All fixture text is invented.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx2
import pytest

from app import config
from app.common import mongo
from app.entrypoints import fastapi as entrypoint
from app.guidance import ids, models, records
from app.guidance.documents import store
from app.search import in_hub, router, search_service

MARKDOWN = "# Claims\n\n## 1 Paying\n\nRepayment, then payment."
BREACHES = "OHF Riverbank and Pond Breaches and Reductions"

# The settings that say where each search is done.
ONE_GUIDE = "feature_search_one_guide"
ALL_TITLES = "feature_search_all_titles"


@pytest.fixture(autouse=True)
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    await mongo_database[records.DOCUMENTS].delete_many({})
    await mongo_database[records.VERSIONS].delete_many({})
    entrypoint.app.dependency_overrides[mongo.get_db] = lambda: mongo_database
    yield mongo_database
    entrypoint.app.dependency_overrides.pop(mongo.get_db, None)


class _FailingSearch:
    """A search whose search service fails, wherever it is asked."""

    async def in_guide(self, *_args: object) -> None:
        raise search_service.SearchServiceError(_UNAVAILABLE)

    async def like(self, *_args: object) -> None:
        raise search_service.SearchServiceError(_UNAVAILABLE)


_UNAVAILABLE = "The search service answered 503"


@pytest.fixture
def failing_search():
    getters = (router.get_guide_search, router.get_title_search)
    for getter in getters:
        entrypoint.app.dependency_overrides[getter] = _FailingSearch
    yield
    for getter in getters:
        entrypoint.app.dependency_overrides.pop(getter, None)


@pytest.fixture
async def client():
    transport = httpx2.ASGITransport(app=entrypoint.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


async def _seed(database) -> tuple[ids.DocumentId, ids.VersionId]:
    document_id = ids.new_document_id()
    version = await records.create_version(
        database,
        models.Version(
            id=ids.new_version_id(),
            document_id=document_id,
            content_url=f"s3://docs/{document_id}/1/content.md",
            created_at=dt.datetime.now(tz=dt.UTC),
        ),
    )
    await records.create(
        database,
        models.Document(
            id=document_id,
            upload_id=ids.UploadId(str(uuid.uuid4())),
            file_id=ids.FileId(str(uuid.uuid4())),
            metadata={},
            created_at=dt.datetime.now(tz=dt.UTC),
        ),
    )
    return document_id, version.id


async def _titled(
    database, *titles: str | None, recorded: bool = True
) -> ids.DocumentId:
    """A guide with a version for each title, the last the newest; its document is
    recorded last, as a conversion records it."""
    document_id = ids.new_document_id()
    first = dt.datetime.now(tz=dt.UTC)
    for minutes, title in enumerate(titles):
        await records.create_version(
            database,
            models.Version(
                id=ids.new_version_id(),
                document_id=document_id,
                content_url=f"s3://docs/{document_id}/{minutes}/content.md",
                created_at=first + dt.timedelta(minutes=minutes),
                title=title,
            ),
        )
    if recorded:
        await records.create(
            database,
            models.Document(
                id=document_id,
                upload_id=ids.UploadId(str(uuid.uuid4())),
                file_id=ids.FileId(str(uuid.uuid4())),
                metadata={},
                created_at=first,
            ),
        )
    return document_id


class TestSearchingOneGuide:
    async def test_answers_where_the_words_are_in_its_latest_version(
        self, client, database, mocker
    ):
        guide, version = await _seed(database)
        mocker.patch.object(store, "read", return_value=MARKDOWN.encode())

        response = await client.get(f"/guides/{guide}/search", params={"q": "pay"})

        assert response.status_code == 200
        assert response.json() == {
            "versionId": str(version),
            "sections": [
                {
                    "anchor": "1-paying",
                    "matches": [
                        {"start": 2, "end": 5, "text": "Pay"},
                        {"start": 11, "end": 14, "text": "pay"},
                        {"start": 25, "end": 28, "text": "pay"},
                    ],
                }
            ],
        }

    async def test_of_a_guide_that_never_was_is_not_found(self, client):
        response = await client.get(
            f"/guides/{ids.new_document_id()}/search", params={"q": "pay"}
        )

        assert response.status_code == 404

    async def test_of_more_characters_than_the_search_service_takes_is_refused(
        self, client
    ):
        response = await client.get(
            f"/guides/{ids.new_document_id()}/search", params={"q": "p" * 501}
        )

        assert response.status_code == 422

    async def test_of_a_guide_whose_document_is_not_yet_recorded_is_not_found(
        self, client, database, mocker
    ):
        """A conversion records its version and content first and the document last;
        until then the guide does not exist, as `GET /guides/{id}` says."""
        document_id = ids.new_document_id()
        await records.create_version(
            database,
            models.Version(
                id=ids.new_version_id(),
                document_id=document_id,
                content_url=f"s3://docs/{document_id}/1/content.md",
                created_at=dt.datetime.now(tz=dt.UTC),
            ),
        )
        mocker.patch.object(store, "read", return_value=MARKDOWN.encode())

        response = await client.get(
            f"/guides/{document_id}/search", params={"q": "pay"}
        )

        assert response.status_code == 404

    async def test_of_a_guide_whose_content_has_gone_is_a_bad_gateway(
        self, client, database, mocker
    ):
        """The record points at something that is not there. Not a 404, which would
        say the guide does not exist when it does."""
        guide, _ = await _seed(database)
        mocker.patch.object(store, "read", return_value=None)

        response = await client.get(f"/guides/{guide}/search", params={"q": "pay"})

        assert response.status_code == 502

    @pytest.mark.usefixtures("failing_search")
    async def test_is_a_bad_gateway_when_the_search_service_fails(self, client):
        response = await client.get(
            f"/guides/{ids.new_document_id()}/search", params={"q": "pay"}
        )

        assert (response.status_code, response.json()) == (
            502,
            {"detail": "The search service is not available"},
        )


class TestFindingGuidesByTitle:
    async def test_answers_the_guides_with_titles_like_the_words(
        self, client, database
    ):
        meant = await _titled(database, BREACHES)
        await _titled(database, "Hedgerow Planting")

        response = await client.get("/search/titles", params={"q": "reducions"})

        assert response.status_code == 200
        assert response.json() == {
            "items": [{"guideId": str(meant), "title": BREACHES}]
        }

    async def test_takes_each_guides_title_from_its_newest_version(
        self, client, database
    ):
        guide = await _titled(database, "Old Reductions Guide", BREACHES)

        response = await client.get("/search/titles", params={"q": "reductions"})

        assert response.json()["items"] == [{"guideId": str(guide), "title": BREACHES}]

    async def test_leaves_out_a_guide_whose_document_is_not_yet_recorded(
        self, client, database
    ):
        await _titled(database, BREACHES, recorded=False)

        response = await client.get("/search/titles", params={"q": "reductions"})

        assert response.json()["items"] == []

    async def test_leaves_out_a_guide_whose_newest_version_has_no_title(
        self, client, database
    ):
        await _titled(database, BREACHES, None)

        response = await client.get("/search/titles", params={"q": "reductions"})

        assert response.json()["items"] == []

    async def test_answers_no_more_guides_than_asked_for(self, client, database):
        for scheme in ("WG", "HG", "MG"):
            await _titled(database, f"{scheme} Breaches and Reductions")

        response = await client.get(
            "/search/titles", params={"q": "reductions", "limit": 2}
        )

        assert len(response.json()["items"]) == 2  # noqa: PLR2004 - the limit

    @pytest.mark.usefixtures("failing_search")
    async def test_is_a_bad_gateway_when_the_search_service_fails(self, client):
        response = await client.get("/search/titles", params={"q": "reductions"})

        assert (response.status_code, response.json()) == (
            502,
            {"detail": "The search service is not available"},
        )


class TestChoosingWhereOneGuideIsSearched:
    def test_the_hub_internal_search_is_chosen_unless_the_environment_says_otherwise(
        self, database
    ):
        chosen = router.get_guide_search(database, s3_client=None)

        assert isinstance(chosen, in_hub.InHubGuideSearch)

    def test_the_search_api_is_chosen_at_its_address_when_the_environment_says_so(
        self, database, mocker
    ):
        _configure(mocker, ONE_GUIDE, search_api_base_url="http://search.test")
        search_api = mocker.patch.object(search_service, "SearchServiceGuideSearch")

        chosen = router.get_guide_search(database, s3_client=None)

        assert chosen is search_api.return_value
        search_api.assert_called_once_with("http://search.test")


class TestChoosingWhereGuidesAreFoundByTitle:
    def test_the_hub_internal_search_is_chosen_unless_the_environment_says_otherwise(
        self, database
    ):
        chosen = router.get_title_search(database)

        assert isinstance(chosen, in_hub.InHubTitleSearch)

    def test_the_search_api_is_chosen_at_its_address_when_the_environment_says_so(
        self, database, mocker
    ):
        _configure(mocker, ALL_TITLES, search_api_base_url="http://search.test")
        search_api = mocker.patch.object(search_service, "SearchServiceTitleSearch")

        chosen = router.get_title_search(database)

        assert chosen is search_api.return_value
        search_api.assert_called_once_with("http://search.test")


def _configure(mocker, feature: str, *, search_api_base_url: str) -> None:
    """The search that `feature` names goes to the search API, at the address given."""
    mocker.patch.object(
        config,
        "get_config",
        return_value=config.get_config().model_copy(
            update={
                feature: config.SearchImplementation.SEARCH_API,
                "search_api_base_url": search_api_base_url,
            }
        ),
    )
