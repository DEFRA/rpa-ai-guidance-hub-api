"""Signing a user in and reading them back, over HTTP, against a real MongoDB."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import httpx2
import pytest

import app.entrypoints.fastapi
from app.common import mongo
from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import records as guidance_records
from app.users import records

TENANT = "00000000-0000-0000-0000-000000000456"


@pytest.fixture(autouse=True)
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[mongo.get_db] = lambda: mongo_database
    yield mongo_database
    fastapi_app.dependency_overrides.pop(mongo.get_db, None)


@pytest.fixture
async def client():
    transport = httpx2.ASGITransport(app=app.entrypoints.fastapi.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


def _signed_in(object_id: str, name: str = "Dev User") -> dict[str, str]:
    return {"tenantId": TENANT, "objectId": object_id, "displayName": name}


async def _user(client) -> str:
    return (await client.post("/users", json=_signed_in(str(uuid.uuid4())))).json()[
        "id"
    ]


async def _guide(
    database, title: str, metadata: dict | None = None
) -> guidance_ids.DocumentId:
    """A guide as stored now, with one version."""
    document_id = guidance_ids.new_document_id()
    now = dt.datetime.now(tz=dt.UTC)
    await guidance_records.create_version(
        database,
        guidance_models.Version(
            id=guidance_ids.new_version_id(),
            document_id=document_id,
            content_url=f"s3://docs/{document_id}/v/content.md",
            title=title,
            version_number="1.0",
            created_at=now,
        ),
    )
    await guidance_records.create(
        database,
        guidance_models.Document(
            id=document_id,
            upload_id=guidance_ids.UploadId(str(uuid.uuid4())),
            file_id=guidance_ids.FileId(str(uuid.uuid4())),
            metadata={"goal": f"About {title}", **(metadata or {})},
            created_at=now,
        ),
    )
    return document_id


class TestSigningIn:
    async def test_a_first_sign_in_creates_the_user(self, client):
        object_id = str(uuid.uuid4())

        response = await client.post("/users", json=_signed_in(object_id))

        body = response.json()
        assert response.status_code == 201
        assert uuid.UUID(body["id"]).version == 7
        assert body["identity"] == {"tenantId": TENANT, "objectId": object_id}
        assert body["displayName"] == "Dev User"
        assert body["createdAt"] is not None

    async def test_signing_in_again_finds_the_user_with_their_latest_name(self, client):
        object_id = str(uuid.uuid4())
        first = (await client.post("/users", json=_signed_in(object_id))).json()

        response = await client.post("/users", json=_signed_in(object_id, "Renamed"))

        assert response.status_code == 200
        assert response.json()["id"] == first["id"]
        assert response.json()["displayName"] == "Renamed"

    @pytest.mark.parametrize("missing", ["tenantId", "objectId"])
    async def test_a_sign_in_without_the_whole_identity_is_refused(
        self, client, missing
    ):
        body = _signed_in(str(uuid.uuid4()))
        del body[missing]

        response = await client.post("/users", json=body)

        assert response.status_code == 422


class TestReadingAUser:
    async def test_a_user_is_read_by_the_hubs_id(self, client):
        signed_in = (await client.post("/users", json=_signed_in("o-1"))).json()

        response = await client.get(f"/users/{signed_in['id']}")

        assert response.status_code == 200
        assert response.json() == signed_in

    async def test_an_unknown_id_is_not_found(self, client):
        response = await client.get(f"/users/{uuid.uuid7()}")

        assert response.status_code == 404

    async def test_an_id_that_is_not_a_uuid_is_refused(self, client):
        response = await client.get("/users/not-a-user")

        assert response.status_code == 422


async def _patch(client, user_id: str, *operations) -> int:
    response = await client.patch(
        f"/users/{user_id}/pins",
        json=list(operations),
        headers={"Content-Type": "application/json-patch+json"},
    )
    return response.status_code


async def _pinned_ids(client, user_id: str) -> list[str]:
    pinned = (await client.get(f"/users/{user_id}")).json()["pinned"]
    return [guide["id"] for guide in pinned]


def _add(guide, at="/0"):
    return {"op": "add", "path": at, "value": str(guide)}


class TestPins:
    async def test_a_guide_pinned_at_the_top_comes_first(self, client, database):
        user_id = await _user(client)
        first, second = (
            await _guide(database, "First"),
            await _guide(database, "Second"),
        )

        assert await _patch(client, user_id, _add(first)) == 204
        assert await _patch(client, user_id, _add(second)) == 204

        assert await _pinned_ids(client, user_id) == [str(second), str(first)]

    async def test_moving_down_the_list_puts_it_where_json_patch_says(
        self, client, database
    ):
        user_id = await _user(client)
        guides = [await _guide(database, f"G{n}") for n in range(3)]
        for guide in guides:
            await _patch(client, user_id, _add(guide, "/-"))

        status = await _patch(
            client,
            user_id,
            {"op": "test", "path": "/0", "value": str(guides[0])},
            {"op": "move", "from": "/0", "path": "/1"},
        )

        assert status == 204
        assert await _pinned_ids(client, user_id) == [
            str(guides[1]),
            str(guides[0]),
            str(guides[2]),
        ]

    async def test_two_pins_at_once_both_hold(self, client, database):
        user_id = await _user(client)
        guides = [await _guide(database, f"P{n}") for n in range(4)]

        statuses = await asyncio.gather(
            *(_patch(client, user_id, _add(guide)) for guide in guides)
        )

        assert statuses == [204] * 4
        assert sorted(await _pinned_ids(client, user_id)) == sorted(
            str(g) for g in guides
        )

    async def test_a_patch_made_on_a_list_that_has_changed_applies_nothing(
        self, client, database
    ):
        user_id = await _user(client)
        kept, other = await _guide(database, "Kept"), await _guide(database, "Other")
        await _patch(client, user_id, _add(kept))

        status = await _patch(
            client,
            user_id,
            {"op": "test", "path": "/0", "value": str(other)},
            {"op": "remove", "path": "/0"},
        )

        assert status == 409
        assert await _pinned_ids(client, user_id) == [str(kept)]

    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "replace", "path": "/0", "value": str(uuid.uuid7())},
            {"op": "remove", "path": "/5"},
            {"op": "remove", "path": "/" + "9" * 5000},
        ],
    )
    async def test_an_operation_that_cannot_apply_is_refused(
        self, client, database, operation
    ):
        user_id = await _user(client)
        await _patch(client, user_id, _add(await _guide(database, "Pinned")))

        assert await _patch(client, user_id, operation) == 422

    async def test_a_guide_already_pinned_is_refused(self, client, database):
        user_id = await _user(client)
        guide = await _guide(database, "Twice")
        await _patch(client, user_id, _add(guide))

        assert await _patch(client, user_id, _add(guide, "/-")) == 422

    async def test_a_guide_that_does_not_exist_is_refused(self, client):
        user_id = await _user(client)

        assert (
            await _patch(client, user_id, _add(guidance_ids.new_document_id())) == 422
        )
        assert await _pinned_ids(client, user_id) == []

    async def test_a_user_there_is_not_is_not_found(self, client, database):
        guide = await _guide(database, "Anyone's")

        assert await _patch(client, str(uuid.uuid7()), _add(guide)) == 404

    async def test_the_user_carries_their_pinned_guides_with_titles_in_order(
        self, client, database
    ):
        user_id = await _user(client)
        first, second = (
            await _guide(database, "First"),
            await _guide(database, "Second"),
        )
        await _patch(client, user_id, _add(first), _add(second))

        user = (await client.get(f"/users/{user_id}")).json()

        assert user["pinned"] == [
            {"id": str(second), "title": "Second"},
            {"id": str(first), "title": "First"},
        ]

    async def test_a_pinned_guide_that_has_gone_is_left_out(self, client, database):
        user_id = await _user(client)
        kept, gone = await _guide(database, "Kept"), await _guide(database, "Gone")
        await _patch(client, user_id, _add(kept), _add(gone))
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        assert await _pinned_ids(client, user_id) == [str(kept)]

    async def test_a_patch_takes_positions_as_the_user_sees_them_past_a_guide_gone(
        self, client, database
    ):
        user_id = await _user(client)
        gone, first, second = (
            await _guide(database, "Gone"),
            await _guide(database, "First"),
            await _guide(database, "Second"),
        )
        await _patch(
            client, user_id, *(_add(guide, "/-") for guide in (gone, first, second))
        )
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        status = await _patch(
            client,
            user_id,
            {"op": "test", "path": "/0", "value": str(first)},
            {"op": "remove", "path": "/0"},
        )

        assert status == 204
        assert await _pinned_ids(client, user_id) == [str(second)]
        stored = await database[records.USERS].find_one({"_id": uuid.UUID(user_id)})
        assert stored["pins"] == [second]


async def _open(client, user_id: str, guide_id) -> int:
    response = await client.post(
        f"/users/{user_id}/recently-opened", json={"guideId": str(guide_id)}
    )
    return response.status_code


async def _opened_ids(database, user_id: str) -> list[guidance_ids.DocumentId]:
    user = await records.find(database, uuid.UUID(user_id))
    return [opened.document_id for opened in user.recently_opened]


class TestRecordingAGuideAsOpened:
    async def test_opening_a_guide_is_recorded_with_nothing_to_answer(
        self, client, database
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims")

        assert await _open(client, user_id, guide) == 204
        assert await _opened_ids(database, user_id) == [guide]

    async def test_a_guide_that_does_not_exist_is_refused(self, client, database):
        user_id = await _user(client)

        assert await _open(client, user_id, guidance_ids.new_document_id()) == 422
        assert await _opened_ids(database, user_id) == []

    async def test_a_user_there_is_not_is_not_found(self, client, database):
        guide = await _guide(database, "Anyone's")

        assert await _open(client, str(uuid.uuid7()), guide) == 404


async def _listed(client, user_id: str, **filters) -> dict:
    return (await client.get(f"/users/{user_id}/pins", params=filters)).json()


class TestThePinnedList:
    async def test_lists_the_pinned_guides_in_the_users_order(self, client, database):
        user_id = await _user(client)
        first, second = (
            await _guide(database, "First"),
            await _guide(database, "Second"),
        )
        await _patch(client, user_id, _add(first), _add(second))

        body = await _listed(client, user_id)

        assert [item["id"] for item in body["items"]] == [str(second), str(first)]

    async def test_a_user_with_no_pins_lists_and_counts_nothing(self, client, database):
        user_id = await _user(client)
        await _guide(
            database, "Not pinned", {"systems": ["CRM"], "audience": ["Caseworker"]}
        )

        body = await _listed(client, user_id)

        assert body["items"] == []
        assert body["counts"] == {"scheme": [], "system": [], "audience": []}

    async def test_the_list_takes_the_hubs_filters_and_counts_its_own_guides(
        self, client, database
    ):
        user_id = await _user(client)
        crm = await _guide(database, "CRM", {"systems": ["CRM"]})
        siti = await _guide(database, "Siti", {"systems": ["Siti Agri"]})
        await _guide(database, "Not pinned", {"systems": ["CRM"]})
        await _patch(client, user_id, _add(crm), _add(siti))

        body = await _listed(client, user_id, system="CRM")

        assert [item["id"] for item in body["items"]] == [str(crm)]
        assert [(o["label"], o["count"]) for o in body["counts"]["system"]] == [
            ("Siti Agri", 1),
            ("CRM", 1),
        ]

    async def test_each_guide_says_where_it_is_among_all_the_pins_whatever_is_shown(
        self, client, database
    ):
        user_id = await _user(client)
        crm = await _guide(database, "CRM", {"systems": ["CRM"]})
        siti = await _guide(database, "Siti", {"systems": ["Siti Agri"]})
        await _patch(client, user_id, _add(crm), _add(siti))

        body = await _listed(client, user_id, system="CRM")

        assert [(item["id"], item["position"]) for item in body["items"]] == [
            (str(crm), 1)
        ]

    async def test_positions_leave_out_a_guide_that_has_gone(self, client, database):
        user_id = await _user(client)
        gone, first, second = (
            await _guide(database, "Gone"),
            await _guide(database, "First"),
            await _guide(database, "Second"),
        )
        await _patch(
            client, user_id, *(_add(guide, "/-") for guide in (gone, first, second))
        )
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        body = await _listed(client, user_id)

        assert [(item["id"], item["position"]) for item in body["items"]] == [
            (str(first), 0),
            (str(second), 1),
        ]

    async def test_no_scheme_chooses_the_pinned_guides_specific_to_no_scheme(
        self, client, database
    ):
        user_id = await _user(client)
        any_scheme = await _guide(database, "Any", {"schemes": []})
        bps = await _guide(database, "BPS", {"schemes": ["Basic Payment Scheme"]})
        await _patch(client, user_id, _add(any_scheme), _add(bps))

        body = await _listed(client, user_id, no_scheme="true")

        assert [item["id"] for item in body["items"]] == [str(any_scheme)]

    async def test_a_pinned_guide_that_has_gone_is_left_out(self, client, database):
        user_id = await _user(client)
        kept, gone = await _guide(database, "Kept"), await _guide(database, "Gone")
        await _patch(client, user_id, _add(kept), _add(gone))
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        body = await _listed(client, user_id)

        assert [item["id"] for item in body["items"]] == [str(kept)]

    async def test_a_filter_value_the_hub_does_not_have_is_refused(self, client):
        user_id = await _user(client)

        response = await client.get(
            f"/users/{user_id}/pins", params={"system": "Martian"}
        )

        assert response.status_code == 422

    async def test_a_user_there_is_not_is_not_found(self, client):
        response = await client.get(f"/users/{uuid.uuid7()}/pins")

        assert response.status_code == 404


async def _recently_opened(client, user_id: str, **filters) -> dict:
    return (
        await client.get(f"/users/{user_id}/recently-opened", params=filters)
    ).json()


class TestTheRecentlyOpenedList:
    async def test_lists_guide_summaries_newest_first_each_with_when_it_was_opened(
        self, client, database
    ):
        user_id = await _user(client)
        older, newer = await _guide(database, "Older"), await _guide(database, "Newer")
        await _open(client, user_id, older)
        await _open(client, user_id, newer)

        items = (await _recently_opened(client, user_id))["items"]

        assert [item["id"] for item in items] == [str(newer), str(older)]
        assert items[0]["metadata"]["goal"] == "About Newer"
        assert items[0]["latestVersion"]["versionNumber"] == "1.0"
        assert items[0]["openedAt"] >= items[1]["openedAt"]

    async def test_a_user_who_has_opened_nothing_lists_and_counts_nothing(
        self, client, database
    ):
        user_id = await _user(client)
        await _guide(database, "Not opened", {"systems": ["CRM"]})

        body = await _recently_opened(client, user_id)

        assert body["items"] == []
        assert body["counts"] == {"scheme": [], "system": [], "audience": []}

    async def test_the_list_takes_the_hubs_filters_and_counts_its_own_guides(
        self, client, database
    ):
        user_id = await _user(client)
        crm = await _guide(database, "CRM", {"systems": ["CRM"]})
        siti = await _guide(database, "Siti", {"systems": ["Siti Agri"]})
        await _guide(database, "Not opened", {"systems": ["CRM"]})
        await _open(client, user_id, crm)
        await _open(client, user_id, siti)

        body = await _recently_opened(client, user_id, system="CRM")

        assert [item["id"] for item in body["items"]] == [str(crm)]
        assert [(o["label"], o["count"]) for o in body["counts"]["system"]] == [
            ("Siti Agri", 1),
            ("CRM", 1),
        ]

    async def test_no_scheme_chooses_the_guides_specific_to_no_scheme(
        self, client, database
    ):
        user_id = await _user(client)
        any_scheme = await _guide(database, "Any", {"schemes": []})
        bps = await _guide(database, "BPS", {"schemes": ["Basic Payment Scheme"]})
        await _open(client, user_id, any_scheme)
        await _open(client, user_id, bps)

        body = await _recently_opened(client, user_id, no_scheme="true")

        assert [item["id"] for item in body["items"]] == [str(any_scheme)]

    async def test_a_guide_that_has_gone_is_left_out(self, client, database):
        user_id = await _user(client)
        kept, gone = await _guide(database, "Kept"), await _guide(database, "Gone")
        await _open(client, user_id, kept)
        await _open(client, user_id, gone)
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        body = await _recently_opened(client, user_id)

        assert [item["id"] for item in body["items"]] == [str(kept)]

    async def test_a_filter_value_the_hub_does_not_have_is_refused(self, client):
        user_id = await _user(client)

        response = await client.get(
            f"/users/{user_id}/recently-opened", params={"system": "Martian"}
        )

        assert response.status_code == 422

    async def test_a_user_there_is_not_is_not_found(self, client):
        response = await client.get(f"/users/{uuid.uuid7()}/recently-opened")

        assert response.status_code == 404
