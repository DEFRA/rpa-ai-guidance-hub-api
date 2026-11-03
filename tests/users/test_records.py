"""Finding or creating a user by who signed in, in a real MongoDB (testcontainers).

What these are for is what only Mongo can show: that one identity is one record, even
when two first sign-ins race to create it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pymongo.errors
import pytest

from app.users import records

TENANT = "6f504113-6b64-43f2-ade9-242e05780007"


@pytest.fixture
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    return mongo_database


def _object_id() -> str:
    return str(uuid.uuid4())


async def _sign_in(database, object_id: str, name: str = "Dev User", tenant=TENANT):
    return await records.find_or_create(
        database,
        tenant_id=tenant,
        object_id=object_id,
        display_name=name,
        now=dt.datetime.now(tz=dt.UTC),
    )


class TestFirstSignIn:
    async def test_creates_the_user_with_an_id_of_the_hubs_own(self, database):
        object_id = _object_id()

        user, created = await _sign_in(database, object_id)

        assert created is True
        assert isinstance(user.id, uuid.UUID)
        assert user.id.version == 7
        assert (user.tenant_id, user.object_id) == (TENANT, object_id)
        assert user.display_name == "Dev User"

    async def test_stores_the_identity_nested_and_the_id_as_a_uuid(self, database):
        object_id = _object_id()
        user, _ = await _sign_in(database, object_id)

        raw = await database[records.USERS].find_one({"_id": user.id})

        assert raw["identity"] == {"tenantId": TENANT, "objectId": object_id}
        assert isinstance(raw["_id"], uuid.UUID)
        assert "createdAt" in raw


class TestSigningInAgain:
    async def test_finds_the_same_user_and_refreshes_their_name(self, database):
        object_id = _object_id()
        first, _ = await _sign_in(database, object_id, "Old Name")

        again, created = await _sign_in(database, object_id, "New Name")

        assert created is False
        assert again.id == first.id
        assert again.created_at == first.created_at
        assert again.display_name == "New Name"
        assert (
            await database[records.USERS].count_documents(
                {"identity.objectId": object_id}
            )
            == 1
        )

    async def test_the_same_object_id_in_another_tenant_is_another_user(self, database):
        object_id = _object_id()
        here, _ = await _sign_in(database, object_id)

        there, created = await _sign_in(database, object_id, tenant=str(uuid.uuid4()))

        assert created is True
        assert there.id != here.id

    async def test_two_first_sign_ins_at_once_make_one_user(self, database):
        object_id = _object_id()

        results = await asyncio.gather(
            *(_sign_in(database, object_id) for _ in range(5))
        )

        assert len({user.id for user, _ in results}) == 1
        assert [created for _, created in results].count(True) == 1
        assert (
            await database[records.USERS].count_documents(
                {"identity.objectId": object_id}
            )
            == 1
        )


class TestLosingTheRaceOnAServerThatDoesNotRetry:
    async def test_finds_the_record_the_winner_made(self, database):
        """MongoDB retries a losing upsert itself, so this stands in a collection
        whose insert loses, as on a server that does not retry."""
        object_id = _object_id()
        winner, _ = await _sign_in(database, object_id)
        real = database[records.USERS]

        class LosesTheRace:
            calls = 0

            async def find_one_and_update(self, query, update, **options):
                LosesTheRace.calls += 1
                if options.get("upsert"):
                    message = "E11000 duplicate key"
                    raise pymongo.errors.DuplicateKeyError(message)
                return await real.find_one_and_update(query, update, **options)

        losing = {records.USERS: LosesTheRace()}

        user, created = await _sign_in(losing, object_id, "Second")

        assert created is False
        assert user.id == winner.id
        assert user.display_name == "Second"
        assert LosesTheRace.calls == 2

    async def test_a_record_gone_before_the_second_look_is_raised(self):
        class LosesTheRaceToNothing:
            async def find_one_and_update(self, _query, _update, **options):
                if options.get("upsert"):
                    message = "E11000 duplicate key"
                    raise pymongo.errors.DuplicateKeyError(message)

        losing = {records.USERS: LosesTheRaceToNothing()}
        object_id = _object_id()

        with pytest.raises(records.VanishedUserError):
            await _sign_in(losing, object_id)


class TestFindingByTheHubsId:
    async def test_finds_the_user(self, database):
        user, _ = await _sign_in(database, _object_id())

        assert await records.find(database, user.id) == user

    async def test_finds_nobody_for_an_unknown_id(self, database):
        assert await records.find(database, uuid.uuid7()) is None


class TestPatchingPins:
    async def test_pins_that_change_under_every_attempt_are_raised(self, database):
        """This stands in a collection where another change lands between each read
        and its write, as a second writer's would, so every write finds the pins
        changed."""
        user, _ = await _sign_in(database, _object_id())
        real = database[records.USERS]

        class ChangedUnderEveryWrite:
            writes = 0

            async def find_one(self, query, projection):
                return await real.find_one(query, projection)

            async def update_one(self, query, update):
                ChangedUnderEveryWrite.writes += 1
                await real.update_one(
                    {"_id": query["_id"]}, {"$push": {"pins": uuid.uuid7()}}
                )
                return await real.update_one(query, update)

        changing = {records.USERS: ChangedUnderEveryWrite()}

        with pytest.raises(records.ListChangingError):
            await records.patch_pins(changing, user.id, list)

        assert ChangedUnderEveryWrite.writes > 1

    async def test_answers_false_for_a_user_there_is_not(self, database):
        assert not await records.patch_pins(database, uuid.uuid7(), list)


class TestIndexes:
    async def test_the_identity_is_unique(self, database):
        indexes = await database[records.USERS].index_information()

        unique = [
            [name for name, _ in index["key"]]
            for index in indexes.values()
            if index.get("unique")
        ]

        assert ["identity.tenantId", "identity.objectId"] in unique
