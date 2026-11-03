"""Signing a user in and reading them back, over HTTP, against a real MongoDB."""

from __future__ import annotations

import uuid

import httpx2
import pytest

from app.common import mongo
from app.entrypoints.fastapi import app
from app.users import records

TENANT = "6f504113-6b64-43f2-ade9-242e05780007"


@pytest.fixture(autouse=True)
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
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


def _signed_in(object_id: str, name: str = "Dev User") -> dict[str, str]:
    return {"tenantId": TENANT, "objectId": object_id, "displayName": name}


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
