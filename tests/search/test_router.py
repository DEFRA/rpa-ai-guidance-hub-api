"""Searching one guide over HTTP, against a real MongoDB; the guide's content is stubbed.

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
from app.search import in_hub, router

MARKDOWN = "# Claims\n\n## 1 Paying\n\nRepayment, then payment."


@pytest.fixture(autouse=True)
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    await mongo_database[records.DOCUMENTS].delete_many({})
    await mongo_database[records.VERSIONS].delete_many({})
    entrypoint.app.dependency_overrides[mongo.get_db] = lambda: mongo_database
    yield mongo_database
    entrypoint.app.dependency_overrides.pop(mongo.get_db, None)


@pytest.fixture
async def client():
    transport = httpx2.ASGITransport(app=entrypoint.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


async def _seed(
    database, *, version_title: str | None, guide_title: str
) -> tuple[ids.DocumentId, ids.VersionId]:
    document_id = ids.new_document_id()
    version = await records.create_version(
        database,
        models.Version(
            id=ids.new_version_id(),
            document_id=document_id,
            content_url=f"s3://docs/{document_id}/1/content.md",
            title=version_title,
            created_at=dt.datetime.now(tz=dt.UTC),
        ),
    )
    await records.create(
        database,
        models.Document(
            id=document_id,
            upload_id=ids.UploadId(str(uuid.uuid4())),
            file_id=ids.FileId(str(uuid.uuid4())),
            metadata={"guideTitle": guide_title},
            created_at=dt.datetime.now(tz=dt.UTC),
        ),
    )
    return document_id, version.id


class TestSearchingOneGuide:
    async def test_answers_where_the_words_are_in_its_latest_version(
        self, client, database, mocker
    ):
        guide, version = await _seed(
            database, version_title="Overpayments", guide_title="Claims"
        )
        mocker.patch.object(store, "read", return_value=MARKDOWN.encode())

        response = await client.get(f"/guides/{guide}/search", params={"q": "pay"})

        assert response.status_code == 200
        assert response.json() == {
            "versionId": str(version),
            "title": [{"start": 4, "end": 7, "text": "pay"}],
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

    async def test_searches_the_title_the_guide_page_shows_when_the_version_has_none(
        self, client, database, mocker
    ):
        guide, _ = await _seed(database, version_title=None, guide_title="Repayments")
        mocker.patch.object(store, "read", return_value=MARKDOWN.encode())

        response = await client.get(f"/guides/{guide}/search", params={"q": "pay"})

        assert response.json()["title"] == [{"start": 2, "end": 5, "text": "pay"}]

    async def test_of_a_guide_that_never_was_is_not_found(self, client):
        response = await client.get(
            f"/guides/{ids.new_document_id()}/search", params={"q": "pay"}
        )

        assert response.status_code == 404

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
                title="Claims",
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
        guide, _ = await _seed(database, version_title="Claims", guide_title="Claims")
        mocker.patch.object(store, "read", return_value=None)

        response = await client.get(f"/guides/{guide}/search", params={"q": "pay"})

        assert response.status_code == 502


class TestChoosingWhereOneGuideIsSearched:
    def test_the_hub_internal_search_is_chosen_unless_the_environment_says_otherwise(
        self, database
    ):
        chosen = router.get_guide_search(database, s3_client=None)

        assert isinstance(chosen, in_hub.InHubGuideSearch)

    def test_the_search_api_is_not_built_yet(self, database, mocker):
        mocker.patch.object(
            config,
            "get_config",
            return_value=config.get_config().model_copy(
                update={
                    "feature_search_one_guide": config.SearchImplementation.SEARCH_API
                }
            ),
        )

        with pytest.raises(NotImplementedError, match="search API"):
            router.get_guide_search(database, s3_client=None)
