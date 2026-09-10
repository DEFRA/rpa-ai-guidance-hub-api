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


@pytest_asyncio.fixture
async def mongo_database(mongo_container: Any) -> AsyncIterator[Any]:
    connection_url = mongo_container.get_connection_url()
    client: AsyncMongoClient = AsyncMongoClient(
        connection_url, uuidRepresentation="standard"
    )

    db = client["test-database"]

    yield db

    await client.drop_database("test-database")
    await client.close()
