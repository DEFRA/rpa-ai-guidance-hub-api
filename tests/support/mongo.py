"""Testcontainers MongoDB support for testing real Mongo storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from testcontainers.mongodb import MongoDbContainer


@pytest.fixture(scope="session")
def mongo_container() -> Any:
    with MongoDbContainer("mongo:7.0") as container:
        yield container


@pytest_asyncio.fixture(scope="session")
async def mongo_database(mongo_container: Any) -> AsyncIterator[Any]:
    """Provide a session-scoped AsyncMongoDB database connected to the test container.

    A session scope ensures we can set a single FastAPI dependency override for
    the whole test session so TestClient-based tests use the same test DB.
    """
    connection_url = mongo_container.get_connection_url()
    client: AsyncMongoClient = AsyncMongoClient(
        connection_url, uuidRepresentation="standard"
    )

    db = client["test-database"]

    yield db

    await client.drop_database("test-database")
    await client.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def override_fastapi_mongo(
    mongo_database: AsyncMongoClient,
) -> AsyncIterator[None]:
    """Autouse fixture that makes the app use the test Mongo client.

    `get_mongo_client` caches its client in the `app.common.mongo.client` module
    global and returns it as-is once set, rather than opening a new connection.
    Pre-populating that global with one wrapping the test container lets the
    real function run unmodified, so the app lifespan (and anything else
    calling `get_mongo_client`) gets the test database instead of attempting a
    real connection to the default `MONGO_URI`. Unit tests of `get_mongo_client`
    itself reset this global per test, so they are unaffected.
    """
    # Import lazily to avoid import-time side effects
    from app.common import mongo as app_mongo

    # The fixture yields a Database object; its `.client` is the AsyncMongoClient
    real_client = mongo_database.client

    # Provide a proxy that forwards to the real client but has a no-op close(),
    # so the app lifespan doesn't close the shared test client. The real
    # client will be closed by the test fixture teardown.
    class _ClientProxy:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, item):
            return getattr(self._wrapped, item)

        async def close(self):
            # no-op: test teardown will close the real client
            return None

    app_mongo.client = _ClientProxy(real_client)
    yield
    app_mongo.client = None
