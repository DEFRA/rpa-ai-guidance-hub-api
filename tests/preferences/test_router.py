"""A user's preferences, over HTTP, against a real MongoDB."""

from __future__ import annotations

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
