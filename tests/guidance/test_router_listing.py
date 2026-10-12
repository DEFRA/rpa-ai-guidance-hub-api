"""Listing, reading and fetching pictures of documents, over HTTP, against a real MongoDB."""

from __future__ import annotations

import datetime as dt
import uuid

import httpx2
import pytest

from app.common import mongo, s3
from app.entrypoints.fastapi import app
from app.guidance import ids, models, records


@pytest.fixture(autouse=True)
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    await mongo_database[records.DOCUMENTS].delete_many({})
    await mongo_database[records.VERSIONS].delete_many({})
    app.dependency_overrides[mongo.get_db] = lambda: mongo_database
    yield mongo_database
    app.dependency_overrides.pop(mongo.get_db, None)


@pytest.fixture
async def client():
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


async def _seed(
    database, metadata: dict | None = None, versions: int = 1
) -> tuple[ids.DocumentId, list[models.Version]]:
    document_id = ids.new_document_id()
    start = dt.datetime.now(tz=dt.UTC)
    made = [
        await records.create_version(
            database,
            models.Version(
                id=ids.new_version_id(),
                document_id=document_id,
                content_url=f"s3://docs/{document_id}/{n}/content.md",
                title=f"Title {n}",
                created_at=start + dt.timedelta(seconds=n),
            ),
        )
        for n in range(versions)
    ]
    await records.create(
        database,
        models.Document(
            id=document_id,
            upload_id=ids.UploadId(str(uuid.uuid4())),
            file_id=ids.FileId(str(uuid.uuid4())),
            metadata=metadata or {},
            created_at=start,
        ),
    )
    return document_id, made


class TestReadingADocument:
    async def test_a_single_version_document_is_returned_with_its_version(
        self, client, database
    ):
        document_id, (version,) = await _seed(database, {"guideTitle": "One"})

        response = await client.get(f"/guides/{document_id}")

        body = response.json()
        assert response.status_code == 200
        assert body["id"] == str(document_id)
        assert body["metadata"] == {"guideTitle": "One"}
        assert [v["id"] for v in body["versions"]] == [str(version.id)]
        assert body["versions"][0]["createdAt"] is not None

    async def test_a_multi_version_document_lists_its_versions_newest_first(
        self, client, database
    ):
        document_id, (oldest, middle, newest) = await _seed(database, versions=3)

        response = await client.get(f"/guides/{document_id}")

        assert [v["id"] for v in response.json()["versions"]] == [
            str(newest.id),
            str(middle.id),
            str(oldest.id),
        ]

    async def test_an_unknown_id_is_not_found(self, client):
        response = await client.get(f"/guides/{ids.new_document_id()}")

        assert response.status_code == 404
        assert "No guide" in response.json()["detail"]


class TestListingDocuments:
    async def test_an_empty_store_gives_an_empty_last_page(self, client):
        response = await client.get("/guides")

        assert response.status_code == 200
        assert response.json() == {"items": [], "nextCursor": None}

    async def test_each_item_carries_its_newest_version(self, client, database):
        document_id, (_, newest) = await _seed(database, versions=2)

        response = await client.get("/guides")

        (item,) = response.json()["items"]
        assert item["id"] == str(document_id)
        assert item["latestVersion"]["id"] == str(newest.id)

    async def test_a_document_without_versions_is_listed_with_no_latest_version(
        self, client, database
    ):
        document_id, _ = await _seed(database, versions=0)

        response = await client.get("/guides")

        (item,) = response.json()["items"]
        assert item["id"] == str(document_id)
        assert item["latestVersion"] is None

    async def test_pages_are_followed_with_the_cursor_until_it_runs_out(
        self, client, database
    ):
        seeded = [(await _seed(database))[0] for _ in range(3)]

        first = (await client.get("/guides", params={"limit": 2})).json()
        second = (
            await client.get(
                "/guides", params={"limit": 2, "cursor": first["nextCursor"]}
            )
        ).json()

        assert [i["id"] for i in first["items"]] == [str(seeded[2]), str(seeded[1])]
        assert first["nextCursor"] == str(seeded[1])
        assert [i["id"] for i in second["items"]] == [str(seeded[0])]
        assert second["nextCursor"] is None

    async def test_metadata_carries_labels_in_place_of_reference_keys(
        self, client, database
    ):
        await _seed(database, {"schemes": ["basic-payment-scheme"], "title": "T"})

        response = await client.get("/guides")

        (item,) = response.json()["items"]
        assert item["metadata"] == {"schemes": ["Basic Payment Scheme"], "title": "T"}

    async def test_a_filter_keeps_only_documents_with_one_of_its_values(
        self, client, database
    ):
        wanted, _ = await _seed(database, {"schemes": ["basic-payment-scheme"]})
        await _seed(database, {"schemes": ["not-specific"]})

        response = await client.get(
            "/guides", params={"scheme": "basic-payment-scheme"}
        )

        assert [i["id"] for i in response.json()["items"]] == [str(wanted)]

    async def test_filters_must_all_match(self, client, database):
        wanted, _ = await _seed(
            database, {"schemes": ["not-specific"], "systems": ["crm"]}
        )
        await _seed(database, {"schemes": ["not-specific"], "systems": ["siti-agri"]})

        response = await client.get(
            "/guides", params={"scheme": "not-specific", "system": "crm"}
        )

        assert [i["id"] for i in response.json()["items"]] == [str(wanted)]

    async def test_a_value_outside_the_reference_data_is_refused(self, client):
        response = await client.get("/guides", params={"audience": "martians"})

        assert response.status_code == 422
        assert "martians" in response.json()["detail"]

    @pytest.mark.parametrize("limit", [0, 101])
    async def test_a_limit_outside_the_allowed_range_is_refused(self, client, limit):
        response = await client.get("/guides", params={"limit": limit})

        assert response.status_code == 422


BUCKET = "rpa-ai-guidance-hub-managed-docs"
PICTURE = "a3f9c1.png"


@pytest.fixture
def bucket(moto_s3):
    moto_s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    app.dependency_overrides[s3.get_s3_client] = lambda: moto_s3
    yield moto_s3
    app.dependency_overrides.pop(s3.get_s3_client, None)


class TestReadingAnAsset:
    async def test_a_picture_is_served_with_its_type(self, client, bucket):
        document_id = ids.new_document_id()
        bucket.put_object(
            Bucket=BUCKET, Key=f"{document_id}/assets/{PICTURE}", Body=b"PNG"
        )

        response = await client.get(f"/guides/{document_id}/assets/{PICTURE}")

        assert response.status_code == 200
        assert response.content == b"PNG"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.usefixtures("bucket")
    async def test_a_picture_the_document_does_not_have_is_not_found(self, client):
        response = await client.get(f"/guides/{ids.new_document_id()}/assets/{PICTURE}")

        assert response.status_code == 404

    async def test_another_documents_picture_is_not_served(self, client, bucket):
        other = ids.new_document_id()
        bucket.put_object(Bucket=BUCKET, Key=f"{other}/assets/{PICTURE}", Body=b"PNG")

        response = await client.get(f"/guides/{ids.new_document_id()}/assets/{PICTURE}")

        assert response.status_code == 404

    @pytest.mark.usefixtures("bucket")
    @pytest.mark.parametrize("name", ["..%2Fsecret.png", "a.b.png", "noextension"])
    async def test_a_name_that_is_not_a_picture_name_is_refused(self, client, name):
        response = await client.get(f"/guides/{ids.new_document_id()}/assets/{name}")

        assert response.status_code in (404, 422)
