"""Deleting and rebuilding the search index, over HTTP, against fakes of the search
service. Who may ask is the auth tests' to show: every documented operation demands a
token (`test_every_documented_operation_demands_a_token`).
"""

from __future__ import annotations

import logging

import httpx2
import pytest

import app.entrypoints.fastapi
from app.admin import router, tasks
from app.common import mongo
from app.search import search_service
from tests.fakes import search_index


@pytest.fixture
def search_admin():
    return search_index.InMemorySearchAdmin(search_service.ResetResult(12, 3))


@pytest.fixture(autouse=True)
def overrides(search_admin):
    """The search service, and the database, are not touched by anything here."""
    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[router.get_search_admin] = lambda: search_admin
    fastapi_app.dependency_overrides[router.get_search_index] = lambda: "index"
    fastapi_app.dependency_overrides[mongo.get_db] = lambda: "database"
    yield
    for dependency in (router.get_search_admin, router.get_search_index, mongo.get_db):
        fastapi_app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def started(mocker):
    return mocker.patch.object(tasks, "start")


@pytest.fixture
async def client():
    transport = httpx2.ASGITransport(app=app.entrypoints.fastapi.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


class TestDeletingTheIndex:
    async def test_answers_what_the_search_service_deleted(self, client, search_admin):
        response = await client.delete(
            "/admin/search-index", headers={"x-requested-by": "user-1"}
        )

        assert response.status_code == 200
        assert response.json() == {"deletedObjects": 12, "deletedStateRecords": 3}
        assert search_admin.resets == 1

    async def test_logs_who_asked_and_what_was_deleted(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="app.admin.router"):
            await client.delete(
                "/admin/search-index", headers={"x-requested-by": "user-1"}
            )

        assert "user-1" in caplog.text
        assert "12 objects and 3 state records" in caplog.text

    async def test_answers_502_when_the_search_service_fails(
        self, client, search_admin
    ):
        search_admin.failure = search_service.SearchServiceError("answered 503")

        response = await client.delete(
            "/admin/search-index", headers={"x-requested-by": "user-1"}
        )

        assert response.status_code == 502  # noqa: PLR2004
        assert "answered 503" not in response.text

    async def test_wants_to_know_who_asked(self, client, search_admin):
        response = await client.delete("/admin/search-index")

        assert response.status_code == 422  # noqa: PLR2004
        assert search_admin.resets == 0


class TestRebuildingTheIndex:
    async def test_starts_the_rebuild_and_answers_at_once(
        self, client, started, search_admin
    ):
        response = await client.post(
            "/admin/search-index/rebuild", json={"requestedBy": "user-1"}
        )

        assert response.status_code == 201  # noqa: PLR2004
        assert response.json() == {"status": "started"}
        started.assert_called_once()
        assert started.call_args.args[1:4] == ("database", "index", "user-1")
        assert search_admin.resets == 0

    async def test_runs_it_with_the_configured_concurrency(self, client, started):
        await client.post("/admin/search-index/rebuild", json={"requestedBy": "u"})

        assert started.call_args.args[4] == 4  # noqa: PLR2004 - the default

    async def test_wants_to_know_who_asked(self, client, started):
        response = await client.post("/admin/search-index/rebuild", json={})

        assert response.status_code == 422  # noqa: PLR2004
        started.assert_not_called()
