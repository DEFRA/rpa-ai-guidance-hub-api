"""Client-side MongoDB timeouts, proved against a real server that stalls.

A MongoDB started with `enableTestCommands` can be told, through the `failCommands`
fail point, to hold the reply to a command for a while. That is a server that has
accepted the connection but does not answer, which is what the timeouts are for: the
unit tests show which deadline is set, and these show that the driver enforces it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pymongo
import pymongo.errors
import pytest
import pytest_asyncio
from testcontainers.community.mongodb import MongoDbContainer

from app.common import mongo_timeouts
from app.guidance import ids, records

_STALL_SECONDS = 4
_SHORT_TIMEOUT = 0.5
# Time for the driver to give up, with room for a slow machine; well short of the stall.
_GIVE_UP_WITHIN = _STALL_SECONDS - 1
_STALLED_COMMANDS = ["find", "aggregate", "insert", "update", "findAndModify"]


@pytest.fixture(scope="module")
def stalling_container() -> Iterator[MongoDbContainer]:
    container = MongoDbContainer("mongo:7.0")
    container.with_command("mongod --setParameter enableTestCommands=1")
    with container:
        yield container


@pytest_asyncio.fixture(scope="module")
async def client(
    stalling_container: MongoDbContainer,
) -> AsyncIterator[pymongo.AsyncMongoClient]:
    connected: pymongo.AsyncMongoClient = pymongo.AsyncMongoClient(
        stalling_container.get_connection_url(), uuidRepresentation="standard"
    )
    await connected.admin.command("ping")
    yield connected
    await connected.close()


@pytest.fixture
def database(client: pymongo.AsyncMongoClient) -> Any:
    return client[f"timeouts-{uuid.uuid4().hex}"]


@pytest_asyncio.fixture
async def stalled(client: pymongo.AsyncMongoClient) -> AsyncIterator[None]:
    """Hold every reply for `_STALL_SECONDS`, until the test is over."""
    await client.admin.command(
        {
            "configureFailPoint": "failCommand",
            "mode": "alwaysOn",
            "data": {
                "failCommands": _STALLED_COMMANDS,
                "blockConnection": True,
                "blockTimeMS": _STALL_SECONDS * 1000,
            },
        }
    )
    yield
    await client.admin.command({"configureFailPoint": "failCommand", "mode": "off"})


async def _times_out(call: Any) -> tuple[mongo_timeouts.MongoTimeoutError, float]:
    started = time.monotonic()
    with pytest.raises(mongo_timeouts.MongoTimeoutError) as raised:
        await call()
    return raised.value, time.monotonic() - started


async def test_an_answering_server_is_not_cut_short(database):
    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def count() -> int:
        return await database["things"].count_documents({})

    assert await count() == 0


@pytest.mark.usefixtures("stalled")
async def test_a_stalled_read_gives_up_at_the_deadline(database):
    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def read() -> Any:
        return await database["things"].find_one({})

    error, elapsed = await _times_out(read)

    assert _SHORT_TIMEOUT <= elapsed < _GIVE_UP_WITHIN
    assert error.timeout == _SHORT_TIMEOUT


@pytest.mark.usefixtures("stalled")
async def test_a_stalled_write_gives_up_at_the_deadline(database):
    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def write() -> None:
        await database["things"].insert_one({"_id": 1})

    _, elapsed = await _times_out(write)

    assert elapsed < _GIVE_UP_WITHIN


@pytest.mark.usefixtures("stalled")
async def test_a_stalled_aggregation_gives_up_at_the_deadline(database):
    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def aggregate() -> Any:
        cursor = await database["things"].aggregate([{"$match": {}}])
        return await cursor.to_list()

    _, elapsed = await _times_out(aggregate)

    assert elapsed < _GIVE_UP_WITHIN


@pytest.mark.usefixtures("stalled")
async def test_the_deadline_covers_all_the_operations_in_the_method(database):
    """The deadline is for the method, not for each operation in it."""
    things = database["things"]

    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def several() -> None:
        for _ in range(10):
            await things.find_one({})

    _, elapsed = await _times_out(several)

    assert elapsed < _GIVE_UP_WITHIN


@pytest.mark.usefixtures("stalled")
async def test_a_timeout_is_logged_with_what_timed_out(database, caplog):
    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def read() -> Any:
        return await database["things"].find_one({})

    await _times_out(read)

    record = next(r for r in caplog.records if r.name == mongo_timeouts.__name__)
    assert record.exception_type == mongo_timeouts.EXCEPTION_TYPE
    assert record.operation.endswith("read")
    assert record.timeout_seconds == _SHORT_TIMEOUT


@pytest.mark.usefixtures("stalled")
async def test_a_repository_method_gives_up_at_its_tier(database):
    """The real thing: `records.find_by_upload`, with the tier it is declared with."""
    upload_id = ids.UploadId(str(uuid.uuid4()))

    error, elapsed = await _times_out(
        lambda: records.find_by_upload(database, upload_id)
    )

    assert error.timeout == mongo_timeouts.FAST
    assert mongo_timeouts.FAST <= elapsed < _GIVE_UP_WITHIN


async def test_a_clash_is_still_a_clash_not_a_timeout(database):
    """Errors that are not timeouts keep their own type, which callers handle."""
    things = database["things"]
    await things.insert_one({"_id": 1})

    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def again() -> None:
        await things.insert_one({"_id": 1})

    with pytest.raises(pymongo.errors.DuplicateKeyError):
        await again()


async def test_an_unreachable_server_fails_at_server_selection():
    """The connection-level guard: no server to talk to fails fast, not after 30s."""
    unreachable: pymongo.AsyncMongoClient = pymongo.AsyncMongoClient(
        "mongodb://127.0.0.1:1",
        serverSelectionTimeoutMS=500,
        connectTimeoutMS=500,
    )
    started = time.monotonic()
    try:
        with pytest.raises(pymongo.errors.ServerSelectionTimeoutError):
            await unreachable.admin.command("ping")
    finally:
        await unreachable.close()

    assert time.monotonic() - started < _GIVE_UP_WITHIN


@pytest.mark.usefixtures("stalled")
async def test_an_inner_deadline_cannot_extend_the_outer_one(database):
    """Methods that call decorated methods keep the shortest deadline, as documented."""

    @mongo_timeouts.mongo(30)
    async def inner() -> Any:
        return await database["things"].find_one({})

    @mongo_timeouts.mongo(_SHORT_TIMEOUT)
    async def outer() -> Any:
        return await inner()

    _, elapsed = await _times_out(outer)

    assert elapsed < _GIVE_UP_WITHIN
