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
        assert body["metadata"] == {"schemeYear": 2026, "guideTitle": "One"}
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

    async def test_it_carries_when_the_document_was_created(self, client, database):
        document_id, _ = await _seed(database)
        stored = await database[records.DOCUMENTS].find_one({"_id": document_id})

        body = (await client.get(f"/guides/{document_id}")).json()

        # As stored: Mongo answers times without their zone, and so does the API.
        assert dt.datetime.fromisoformat(body["createdAt"]) == stored["createdAt"]

    async def test_each_version_answers_its_number_and_none_from_before_numbers(
        self, client, database
    ):
        document_id, (older, newer) = await _seed(database, versions=2)
        await database[records.VERSIONS].update_one(
            {"_id": newer.id}, {"$set": {"versionNumber": "2.0"}}
        )
        await database[records.VERSIONS].update_one(
            {"_id": older.id}, {"$unset": {"versionNumber": ""}}
        )

        body = (await client.get(f"/guides/{document_id}")).json()

        assert [v["versionNumber"] for v in body["versions"]] == ["2.0", None]

    async def test_an_unknown_id_is_not_found(self, client):
        response = await client.get(f"/guides/{ids.new_document_id()}")

        assert response.status_code == 404
        assert "No guide" in response.json()["detail"]


class TestListingDocuments:
    async def test_an_empty_store_gives_an_empty_last_page(self, client):
        response = await client.get("/guides")

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["nextCursor"] is None

    async def test_each_item_carries_its_newest_version(self, client, database):
        document_id, (_, newest) = await _seed(database, versions=2)

        response = await client.get("/guides")

        (item,) = response.json()["items"]
        assert item["id"] == str(document_id)
        assert item["latestVersion"]["id"] == str(newest.id)

    async def test_each_item_carries_when_its_document_was_created(
        self, client, database
    ):
        document_id, _ = await _seed(database)
        stored = await database[records.DOCUMENTS].find_one({"_id": document_id})

        (item,) = (await client.get("/guides")).json()["items"]

        # As stored: Mongo answers times without their zone, and so does the API.
        assert dt.datetime.fromisoformat(item["createdAt"]) == stored["createdAt"]

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

    async def test_a_filter_keeps_only_documents_with_one_of_its_values(
        self, client, database
    ):
        wanted, _ = await _seed(database, {"schemes": ["Basic Payment Scheme"]})
        await _seed(database, {"schemes": ["Countryside Stewardship"]})

        response = await client.get(
            "/guides", params={"scheme": "Basic Payment Scheme"}
        )

        assert [i["id"] for i in response.json()["items"]] == [str(wanted)]

    async def test_filters_must_all_match(self, client, database):
        wanted, _ = await _seed(
            database, {"schemes": ["Countryside Stewardship"], "systems": ["CRM"]}
        )
        await _seed(
            database, {"schemes": ["Countryside Stewardship"], "systems": ["Siti Agri"]}
        )

        response = await client.get(
            "/guides", params={"scheme": "Countryside Stewardship", "system": "CRM"}
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


async def _add_version(database, document_id) -> None:
    """A newer version of a document, made now."""
    await records.create_version(
        database,
        models.Version(
            id=ids.new_version_id(),
            document_id=document_id,
            content_url=f"s3://docs/{document_id}/later/content.md",
            title="Later",
            created_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=1),
        ),
    )


class TestSortingDocuments:
    async def test_the_default_order_is_by_when_each_last_changed_newest_first(
        self, client, database
    ):
        changed_later, _ = await _seed(database)
        changed_earlier, _ = await _seed(database)
        await _add_version(database, changed_later)

        items = (await client.get("/guides")).json()["items"]

        assert [i["id"] for i in items] == [str(changed_later), str(changed_earlier)]

    async def test_updated_oldest_puts_the_one_that_changed_longest_ago_first(
        self, client, database
    ):
        changed_later, _ = await _seed(database)
        changed_earlier, _ = await _seed(database)
        await _add_version(database, changed_later)

        response = await client.get("/guides", params={"sort": "updated-oldest"})

        assert [i["id"] for i in response.json()["items"]] == [
            str(changed_earlier),
            str(changed_later),
        ]

    async def test_a_cursor_that_is_not_a_document_is_refused(self, client):
        response = await client.get(
            "/guides", params={"cursor": str(ids.new_document_id())}
        )

        assert response.status_code == 422

    async def test_a_sort_that_is_not_offered_is_refused(self, client):
        response = await client.get("/guides", params={"sort": "title"})

        assert response.status_code == 422

    @pytest.mark.parametrize("sort", ["updated-newest", "updated-oldest"])
    async def test_pages_follow_the_order_to_the_last(self, client, database, sort):
        seeded = [(await _seed(database))[0] for _ in range(3)]
        await _add_version(database, seeded[0])
        everything = [
            i["id"]
            for i in (await client.get("/guides", params={"sort": sort})).json()[
                "items"
            ]
        ]

        first = (await client.get("/guides", params={"sort": sort, "limit": 2})).json()
        second = (
            await client.get(
                "/guides",
                params={"sort": sort, "limit": 2, "cursor": first["nextCursor"]},
            )
        ).json()

        assert [i["id"] for i in first["items"] + second["items"]] == everything
        assert second["nextCursor"] is None


class TestCountingEachOption:
    async def test_each_filter_counts_the_values_the_guides_have_in_the_reference_datas_order(
        self, client, database
    ):
        await _seed(
            database,
            {
                "schemes": ["Basic Payment Scheme"],
                "systems": ["CRM"],
                "audience": ["Caseworker"],
            },
        )
        await _seed(
            database,
            {
                "schemes": ["Basic Payment Scheme", "Countryside Stewardship"],
                "systems": ["Siti Agri"],
            },
        )

        counts = (await client.get("/guides")).json()["counts"]

        assert counts == {
            "scheme": [
                {
                    "label": "Basic Payment Scheme",
                    "shortLabel": "BPS",
                    "count": 2,
                    "retired": False,
                },
                {
                    "label": "Countryside Stewardship",
                    "shortLabel": None,
                    "count": 1,
                    "retired": False,
                },
            ],
            "system": [
                {
                    "label": "Siti Agri",
                    "shortLabel": None,
                    "count": 1,
                    "retired": False,
                },
                {"label": "CRM", "shortLabel": None, "count": 1, "retired": False},
            ],
            "audience": [
                {
                    "label": "Caseworker",
                    "shortLabel": None,
                    "count": 1,
                    "retired": False,
                }
            ],
        }

    async def test_a_filters_counts_are_given_the_other_filters_chosen(
        self, client, database
    ):
        await _seed(database, {"schemes": ["Basic Payment Scheme"], "systems": ["CRM"]})
        await _seed(
            database, {"schemes": ["Countryside Stewardship"], "systems": ["Siti Agri"]}
        )

        counts = (await client.get("/guides", params={"system": "CRM"})).json()[
            "counts"
        ]

        assert [(o["label"], o["count"]) for o in counts["scheme"]] == [
            ("Basic Payment Scheme", 1)
        ]
        assert [(o["label"], o["count"]) for o in counts["system"]] == [
            ("Siti Agri", 1),
            ("CRM", 1),
        ]

    async def test_a_chosen_value_no_guide_has_is_counted_at_nothing(
        self, client, database
    ):
        await _seed(database, {"audience": ["Caseworker"]})

        body = (await client.get("/guides", params={"audience": "Customer"})).json()

        assert body["items"] == []
        assert [(o["label"], o["count"]) for o in body["counts"]["audience"]] == [
            ("Caseworker", 1),
            ("Customer", 0),
        ]

    async def test_a_value_the_reference_data_no_longer_has_comes_last_marked_retired(
        self, client, database
    ):
        await _seed(
            database, {"schemes": ["Retired Scheme", "Countryside Stewardship"]}
        )

        counts = (await client.get("/guides")).json()["counts"]

        assert counts["scheme"] == [
            {
                "label": "Countryside Stewardship",
                "shortLabel": None,
                "count": 1,
                "retired": False,
            },
            {
                "label": "Retired Scheme",
                "shortLabel": None,
                "count": 1,
                "retired": True,
            },
        ]

    async def test_a_guide_with_a_value_twice_is_counted_once(self, client, database):
        await _seed(database, {"systems": ["CRM", "CRM"]})

        counts = (await client.get("/guides")).json()["counts"]

        assert [(o["label"], o["count"]) for o in counts["system"]] == [("CRM", 1)]

    async def test_a_value_stored_as_anything_but_words_is_not_counted(
        self, client, database
    ):
        """Details are stored as sent, so one sent as an object or a number must not
        break the listing of every guide."""
        await _seed(database, {"schemes": [{"label": "Basic Payment Scheme"}]})
        await _seed(database, {"schemes": ["Basic Payment Scheme"], "systems": [7]})

        response = await client.get("/guides")

        assert response.status_code == 200
        assert response.json()["counts"]["scheme"] == [
            {
                "label": "Basic Payment Scheme",
                "shortLabel": "BPS",
                "count": 1,
                "retired": False,
            }
        ]
        assert response.json()["counts"]["system"] == []

    async def test_with_no_guides_nothing_is_counted(self, client):
        assert (await client.get("/guides")).json()["counts"] == {
            "scheme": [],
            "system": [],
            "audience": [],
        }


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

    @pytest.mark.parametrize("name", ["a3f9.svg", "a3f9.gif", "a3f9.html", "a3f9.xyz"])
    async def test_a_file_that_is_not_an_allowed_image_type_is_not_served(
        self, client, bucket, name
    ):
        document_id = ids.new_document_id()
        bucket.put_object(
            Bucket=BUCKET, Key=f"{document_id}/assets/{name}", Body=b"<svg/>"
        )

        response = await client.get(f"/guides/{document_id}/assets/{name}")

        assert response.status_code == 404


class TestGuidesWithNoScheme:
    async def test_are_counted_as_an_entry_of_their_own_after_the_schemes(
        self, client, database
    ):
        await _seed(database, {"schemes": ["Basic Payment Scheme"]})
        await _seed(database, {"schemes": []})

        counts = (await client.get("/guides")).json()["counts"]

        assert counts["scheme"] == [
            {
                "label": "Basic Payment Scheme",
                "shortLabel": "BPS",
                "count": 1,
                "retired": False,
            },
            {"label": None, "shortLabel": None, "count": 1, "retired": False},
        ]

    async def test_are_chosen_by_no_scheme_alone_or_beside_schemes(
        self, client, database
    ):
        bps, _ = await _seed(database, {"schemes": ["Basic Payment Scheme"]})
        await _seed(database, {"schemes": ["Countryside Stewardship"]})
        none, _ = await _seed(database, {"schemes": []})

        alone = await client.get("/guides", params={"no_scheme": "true"})
        beside = await client.get(
            "/guides", params={"no_scheme": "true", "scheme": "Basic Payment Scheme"}
        )

        assert [i["id"] for i in alone.json()["items"]] == [str(none)]
        assert [i["id"] for i in beside.json()["items"]] == [str(none), str(bps)]
        assert (None, 1) in [
            (o["label"], o["count"]) for o in alone.json()["counts"]["scheme"]
        ]

    async def test_include_those_stored_with_the_words_not_scheme_specific(
        self, client, database
    ):
        stored, _ = await _seed(database, {"schemes": ["Not scheme-specific"]})

        body = (await client.get("/guides", params={"no_scheme": "true"})).json()
        read = (await client.get(f"/guides/{stored}")).json()

        assert [i["id"] for i in body["items"]] == [str(stored)]
        assert [i["metadata"]["schemes"] for i in body["items"]] == [[]]
        assert read["metadata"]["schemes"] == []
        assert body["counts"]["scheme"] == [
            {"label": None, "shortLabel": None, "count": 1, "retired": False}
        ]


class TestTheSchemeYear:
    async def test_is_kept_with_the_other_details(self, client, database):
        given, _ = await _seed(database, {"guideTitle": "Given", "schemeYear": 2026})

        read = (await client.get(f"/guides/{given}")).json()

        assert read["metadata"] == {"guideTitle": "Given", "schemeYear": 2026}

    async def test_is_2026_for_a_guide_stored_without_one(self, client, database):
        since, _ = await _seed(database, {"guideTitle": "Since"})

        items = (await client.get("/guides")).json()["items"]

        assert {i["id"]: i["metadata"]["schemeYear"] for i in items} == {
            str(since): 2026
        }
