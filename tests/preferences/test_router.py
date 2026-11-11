"""A user's preferences, over HTTP, against a real MongoDB."""

from __future__ import annotations

import asyncio
import uuid

import httpx2
import pytest

import app.entrypoints.fastapi
from app.common import mongo
from app.preferences import records


@pytest.fixture(autouse=True)
async def database(mongo_database):
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


async def _keep(database, user_id: uuid.UUID, preferences: dict) -> None:
    """Preferences as the service keeps them, written there directly."""
    await database[records.PREFERENCES].insert_one({"_id": user_id, **preferences})


class TestReadingPreferences:
    async def test_a_user_who_has_set_none_has_each_one_not_set(self, client):
        response = await client.get(f"/preferences/{uuid.uuid7()}")

        assert response.status_code == 200
        assert response.json() == {
            "guideFormat": None,
            "readingWidth": None,
            "contentsWidth": None,
            "guideInformationShown": None,
            "sideNavCollapsed": None,
            "sideNavWidth": None,
            "sideNavPinnedCollapsed": None,
            "sideNavBookmarksCollapsed": None,
        }

    async def test_a_user_has_the_preferences_kept_for_them(self, client, database):
        user_id = uuid.uuid7()
        await _keep(database, user_id, {"guideFormat": "stepper", "sideNavWidth": 360})

        preferences = (await client.get(f"/preferences/{user_id}")).json()

        assert preferences["guideFormat"] == "stepper"
        assert preferences["sideNavWidth"] == 360
        assert preferences["readingWidth"] is None

    async def test_a_kept_preference_the_api_no_longer_takes_is_not_set(
        self, client, database
    ):
        user_id = uuid.uuid7()
        await _keep(
            database,
            user_id,
            {"contentsWidth": 9999, "fontSize": 12, "sideNavWidth": 300},
        )

        preferences = (await client.get(f"/preferences/{user_id}")).json()

        assert preferences["contentsWidth"] is None
        assert "fontSize" not in preferences
        assert preferences["sideNavWidth"] == 300

    async def test_an_id_that_is_not_a_uuid_is_refused(self, client):
        response = await client.get("/preferences/not-a-user")

        assert response.status_code == 422


async def _prefer(client, user_id: uuid.UUID, preferences) -> httpx2.Response:
    return await client.patch(
        f"/preferences/{user_id}",
        json=preferences,
        headers={"Content-Type": "application/merge-patch+json"},
    )


async def _preferences(client, user_id: uuid.UUID) -> dict:
    return (await client.get(f"/preferences/{user_id}")).json()


class TestChangingPreferences:
    async def test_a_merge_patch_sets_preferences_and_answers_them_all(self, client):
        user_id = uuid.uuid7()
        await _prefer(client, user_id, {"guideFormat": "stepper", "contentsWidth": 412})

        response = await _prefer(client, user_id, {"sideNavCollapsed": True})

        assert response.status_code == 200
        assert response.json() == {
            "guideFormat": "stepper",
            "readingWidth": None,
            "contentsWidth": 412,
            "guideInformationShown": None,
            "sideNavCollapsed": True,
            "sideNavWidth": None,
            "sideNavPinnedCollapsed": None,
            "sideNavBookmarksCollapsed": None,
        }

    async def test_the_preferences_set_are_kept(self, client):
        user_id = uuid.uuid7()

        await _prefer(
            client,
            user_id,
            {
                "readingWidth": "wide",
                "guideInformationShown": True,
                "sideNavWidth": 360,
                "sideNavPinnedCollapsed": True,
                "sideNavBookmarksCollapsed": False,
            },
        )

        preferences = await _preferences(client, user_id)
        assert preferences["readingWidth"] == "wide"
        assert preferences["guideInformationShown"] is True
        assert preferences["sideNavWidth"] == 360
        assert preferences["sideNavPinnedCollapsed"] is True
        assert preferences["sideNavBookmarksCollapsed"] is False

    async def test_null_takes_a_preference_back_to_its_default(self, client):
        user_id = uuid.uuid7()
        await _prefer(client, user_id, {"guideFormat": "stepper", "sideNavWidth": 300})

        response = await _prefer(client, user_id, {"guideFormat": None})

        assert response.json()["guideFormat"] is None
        assert response.json()["sideNavWidth"] == 300

    async def test_an_empty_patch_answers_the_preferences_as_they_are(self, client):
        user_id = uuid.uuid7()
        await _prefer(client, user_id, {"guideFormat": "stepper"})

        response = await _prefer(client, user_id, {})

        assert response.status_code == 200
        assert response.json()["guideFormat"] == "stepper"

    async def test_two_changes_at_once_are_both_kept(self, client):
        user_id = uuid.uuid7()
        await _prefer(client, user_id, {"guideFormat": "stepper"})

        await asyncio.gather(
            _prefer(client, user_id, {"contentsWidth": 300}),
            _prefer(client, user_id, {"sideNavWidth": 300}),
        )

        preferences = await _preferences(client, user_id)
        assert preferences["contentsWidth"] == 300
        assert preferences["sideNavWidth"] == 300

    async def test_each_user_has_their_own(self, client):
        mine, theirs = uuid.uuid7(), uuid.uuid7()

        await _prefer(client, mine, {"guideFormat": "stepper"})

        assert (await _preferences(client, theirs))["guideFormat"] is None

    @pytest.mark.parametrize(
        "preferences",
        [
            {"fontSize": 12},
            {"guideFormat": "scroll"},
            {"readingWidth": "huge"},
            {"contentsWidth": 239},
            {"contentsWidth": 561},
            {"contentsWidth": "300"},
            {"guideInformationShown": "yes"},
            {"sideNavCollapsed": 1},
            {"sideNavWidth": 219},
            {"sideNavWidth": 441},
            {"sideNavPinnedCollapsed": "yes"},
            {"sideNavBookmarksCollapsed": 0},
            [],
        ],
    )
    async def test_a_preference_there_is_not_or_a_value_it_cannot_take_is_refused(
        self, client, preferences
    ):
        response = await _prefer(client, uuid.uuid7(), preferences)

        assert response.status_code == 422

    async def test_a_refused_patch_changes_nothing(self, client):
        user_id = uuid.uuid7()

        await _prefer(
            client, user_id, {"guideFormat": "stepper", "contentsWidth": 9999}
        )

        assert (await _preferences(client, user_id))["guideFormat"] is None
