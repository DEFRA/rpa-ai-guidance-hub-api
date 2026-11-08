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
from app.users import router as users_router

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
    database, title: str, metadata: dict | None = None, content_url: str | None = None
) -> guidance_ids.DocumentId:
    """A guide as stored now, with one version."""
    document_id = guidance_ids.new_document_id()
    now = dt.datetime.now(tz=dt.UTC)
    await guidance_records.create_version(
        database,
        guidance_models.Version(
            id=guidance_ids.new_version_id(),
            document_id=document_id,
            content_url=content_url or f"s3://docs/{document_id}/v/content.md",
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


def _add(guide, at="/0", **section):
    return {"op": "add", "path": at, "value": {"guideId": str(guide), **section}}


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
            {"op": "test", "path": "/0/guideId", "value": str(guides[0])},
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
            {"op": "test", "path": "/0/guideId", "value": str(other)},
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

        whole_guide = {"sectionId": None, "section": None, "heading": None}
        assert user["pinned"] == [
            {"id": str(second), "title": "Second", **whole_guide},
            {"id": str(first), "title": "First", **whole_guide},
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
            {"op": "test", "path": "/0/guideId", "value": str(first)},
            {"op": "remove", "path": "/0"},
        )

        assert status == 204
        assert await _pinned_ids(client, user_id) == [str(second)]
        stored = await database[records.USERS].find_one({"_id": uuid.UUID(user_id)})
        assert stored["pins"] == [
            {"documentId": second, "sectionId": None, "heading": None}
        ]

    async def test_a_section_pinned_by_its_anchor_is_answered_with_its_heading(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch(client, user_id, _add(guide))

        status = await _patch(
            client, user_id, _add(guide, section="2-two", heading="2 Two")
        )

        assert status == 204
        pinned = (await client.get(f"/users/{user_id}")).json()["pinned"]
        assert [
            {key: pin[key] for key in ("id", "title", "section", "heading")}
            for pin in pinned
        ] == [
            {
                "id": str(guide),
                "title": "Claims",
                "section": "2-two",
                "heading": "2 Two",
            },
            {"id": str(guide), "title": "Claims", "section": None, "heading": None},
        ]
        assert uuid.UUID(pinned[0]["sectionId"]).version == 7
        assert pinned[1]["sectionId"] is None

    async def test_a_pinned_section_is_unpinned_by_its_id(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch(client, user_id, _add(guide, section="1-one", heading="1 One"))
        pinned = (await client.get(f"/users/{user_id}")).json()["pinned"]

        status = await _patch(
            client,
            user_id,
            {"op": "test", "path": "/0/guideId", "value": str(guide)},
            {"op": "test", "path": "/0/sectionId", "value": pinned[0]["sectionId"]},
            {"op": "remove", "path": "/0"},
        )

        assert status == 204
        assert await _pinned_ids(client, user_id) == []

    async def test_a_section_the_guide_does_not_have_is_refused(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        assert await _patch(client, user_id, _add(guide, section="9-gone")) == 422
        assert await _pinned_ids(client, user_id) == []


async def _open(client, user_id: str, guide_id) -> int:
    response = await client.post(
        f"/users/{user_id}/recently-opened", json={"guideId": str(guide_id)}
    )
    return response.status_code


async def _opened_ids(database, user_id: str) -> list[guidance_ids.DocumentId]:
    user = await records.find(database, uuid.UUID(user_id))
    return [opened.document_id for opened in user.recently_opened]


# A guide's content, as stored: its sections are "1 One" at `1-one` and "2 Two" at
# `2-two`, in that order.
CONTENT = "# Claims\n\n## 1 One\n\nText one.\n\n## 2 Two\n\nText two.\n"


@pytest.fixture
def content_url(tmp_path) -> str:
    stored = tmp_path / "content.md"
    stored.write_text(CONTENT)
    return stored.as_uri()


@pytest.fixture
def sbis_switched_on():
    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[users_router.sbi_bookmarks] = lambda: True
    yield
    fastapi_app.dependency_overrides.pop(users_router.sbi_bookmarks, None)


async def _patch_cases(client, user_id: str, *operations) -> httpx2.Response:
    return await client.patch(
        f"/users/{user_id}/cases",
        json=list(operations),
        headers={"Content-Type": "application/json-patch+json"},
    )


async def _cases(client, user_id: str) -> list[dict]:
    return (await client.get(f"/users/{user_id}")).json()["cases"]


def _new_case(guide, section="2-two", heading="2 Two", ref="W1234567", kind="case"):
    return {
        "op": "add",
        "path": "/-",
        "value": {
            "type": kind,
            "ref": ref,
            "bookmarks": [
                {"guideId": str(guide), "section": section, "heading": heading}
            ],
        },
    }


class TestCaseBookmarks:
    async def test_a_section_bookmarked_to_a_new_case_comes_back_under_the_case(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(client, user_id, _new_case(guide, ref="w1234567"))

        assert response.status_code == 204
        [case] = await _cases(client, user_id)
        assert uuid.UUID(case["key"]).version == 4
        assert (case["type"], case["ref"]) == ("case", "W1234567")
        [bookmarked] = case["guides"]
        assert (bookmarked["id"], bookmarked["title"]) == (str(guide), "Claims")
        assert [
            (b["section"], b["heading"], b["position"]) for b in bookmarked["bookmarks"]
        ] == [("2-two", "2 Two", 0)]

    async def test_a_bookmarked_section_keeps_the_id_its_guide_gave_it(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        await _patch_cases(client, user_id, _new_case(guide))

        version = await database[guidance_records.VERSIONS].find_one(
            {"documentId": guide}
        )
        given = {each["anchor"]: str(each["id"]) for each in version["sectionIds"]}
        [case] = await _cases(client, user_id)
        [bookmark] = case["guides"][0]["bookmarks"]
        assert list(given) == ["1-one", "2-two"]
        assert bookmark["sectionId"] == given["2-two"]

    async def test_a_case_s_sections_come_in_the_guide_s_order(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(guide, "2-two", "2 Two"))

        await _patch_cases(
            client,
            user_id,
            {
                "op": "add",
                "path": "/0/bookmarks/-",
                "value": {
                    "guideId": str(guide),
                    "section": "1-one",
                    "heading": "1 One",
                },
            },
        )

        [case] = await _cases(client, user_id)
        assert [
            (b["section"], b["position"]) for b in case["guides"][0]["bookmarks"]
        ] == [("1-one", 1), ("2-two", 0)]

    async def test_a_bookmark_added_to_a_case_keeps_its_key(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(guide, "2-two"))
        [before] = await _cases(client, user_id)

        await _patch_cases(
            client,
            user_id,
            {"op": "test", "path": "/0/key", "value": before["key"]},
            {
                "op": "add",
                "path": "/0/bookmarks/-",
                "value": {"guideId": str(guide), "section": "1-one"},
            },
        )

        [after] = await _cases(client, user_id)
        assert after["key"] == before["key"]

    async def test_a_patch_on_cases_that_have_changed_since_is_a_conflict(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(guide))

        response = await _patch_cases(
            client,
            user_id,
            {"op": "test", "path": "/0/key", "value": str(uuid.uuid4())},
            {"op": "remove", "path": "/0"},
        )

        assert response.status_code == 409
        assert len(await _cases(client, user_id)) == 1

    async def test_a_section_the_guide_has_not_is_refused(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(client, user_id, _new_case(guide, "9-never"))

        assert response.status_code == 422
        assert await _cases(client, user_id) == []

    async def test_a_section_id_of_another_guide_is_refused(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        other = await _guide(database, "Payments", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(other, "1-one"))
        [before] = await _cases(client, user_id)
        [bookmark] = before["guides"][0]["bookmarks"]
        operation = _new_case(guide, ref="W7654321")
        operation["value"]["bookmarks"][0] |= {
            "section": None,
            "sectionId": bookmark["sectionId"],
        }

        response = await _patch_cases(client, user_id, operation)

        assert response.status_code == 422
        assert await _cases(client, user_id) == [before]

    async def test_a_bookmark_moved_to_a_guide_there_is_not_is_refused(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(guide))
        [before] = await _cases(client, user_id)

        response = await _patch_cases(
            client,
            user_id,
            {
                "op": "add",
                "path": "/0/bookmarks/0/guideId",
                "value": str(uuid.uuid7()),
            },
        )

        assert response.status_code == 422
        assert await _cases(client, user_id) == [before]

    async def test_a_guide_there_is_not_is_refused(self, client):
        user_id = await _user(client)

        response = await _patch_cases(client, user_id, _new_case(uuid.uuid7()))

        assert response.status_code == 422

    async def test_a_version_whose_guide_was_never_saved_is_refused(
        self, client, database, content_url
    ):
        """Saving a guide writes its version before the guide, so a failure between
        the two leaves a version that nothing lists, and a bookmark of it would never
        be seen."""
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)
        await database[guidance_records.DOCUMENTS].delete_one({"_id": guide})

        response = await _patch_cases(client, user_id, _new_case(guide))

        assert response.status_code == 422
        assert await _cases(client, user_id) == []

    async def test_a_bookmark_whose_guide_has_gone_stays_when_its_case_changes(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        gone = await _guide(database, "Claims", content_url=content_url)
        other = await _guide(database, "Payments", content_url=content_url)
        await _patch_cases(client, user_id, _new_case(gone))
        await database[guidance_records.DOCUMENTS].delete_one({"_id": gone})

        response = await _patch_cases(
            client,
            user_id,
            {
                "op": "add",
                "path": "/0/bookmarks/-",
                "value": {"guideId": str(other), "section": "1-one"},
            },
        )

        assert response.status_code == 204
        stored = await database[records.USERS].find_one({"_id": uuid.UUID(user_id)})
        [case] = stored["cases"]
        assert [b["documentId"] for b in case["bookmarks"]] == [gone, other]

    async def test_a_reference_that_is_not_one_is_refused(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(client, user_id, _new_case(guide, ref="W123"))

        assert response.status_code == 422

    async def test_an_sbi_is_refused_while_sbis_are_switched_off(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(
            client, user_id, _new_case(guide, ref="123456789", kind="sbi")
        )

        assert response.status_code == 422

    @pytest.mark.usefixtures("sbis_switched_on")
    async def test_an_sbi_is_bookmarked_to_once_sbis_are_switched_on(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(
            client, user_id, _new_case(guide, ref="123456789", kind="sbi")
        )

        assert response.status_code == 204
        [case] = await _cases(client, user_id)
        assert (case["type"], case["ref"]) == ("sbi", "123456789")

    async def test_a_user_there_is_not_is_not_found(
        self, client, database, content_url
    ):
        guide = await _guide(database, "Claims", content_url=content_url)

        response = await _patch_cases(client, str(uuid.uuid7()), _new_case(guide))

        assert response.status_code == 404

    async def test_a_user_with_no_cases_has_none(self, client):
        user_id = await _user(client)

        assert await _cases(client, user_id) == []


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

    async def test_lists_a_pinned_section_with_its_anchor_and_heading_in_its_place(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        sectioned = await _guide(database, "Claims", content_url=content_url)
        whole = await _guide(database, "Whole")
        await _patch(
            client,
            user_id,
            _add(whole),
            _add(sectioned, section="1-one", heading="1 One"),
        )

        body = await _listed(client, user_id)

        assert [
            {key: item[key] for key in ("id", "position", "section", "heading")}
            for item in body["items"]
        ] == [
            {
                "id": str(sectioned),
                "position": 0,
                "section": "1-one",
                "heading": "1 One",
            },
            {"id": str(whole), "position": 1, "section": None, "heading": None},
        ]
        assert uuid.UUID(body["items"][0]["sectionId"]).version == 7
        assert body["items"][1]["sectionId"] is None

    async def test_a_guide_pinned_whole_and_by_a_section_is_listed_for_each_but_counted_once(
        self, client, database, content_url
    ):
        user_id = await _user(client)
        crm = await _guide(
            database, "CRM", {"systems": ["CRM"]}, content_url=content_url
        )
        await _patch(
            client, user_id, _add(crm), _add(crm, section="1-one", heading="1 One")
        )

        body = await _listed(client, user_id, system="CRM")

        assert [(item["id"], item["section"]) for item in body["items"]] == [
            (str(crm), "1-one"),
            (str(crm), None),
        ]
        assert [(o["label"], o["count"]) for o in body["counts"]["system"]] == [
            ("CRM", 1)
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
