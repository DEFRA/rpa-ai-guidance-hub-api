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

from app.users import models, records

TENANT = "00000000-0000-0000-0000-000000000456"


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
                pin = models.Pin(document_id=uuid.uuid7()).to_document()
                await real.update_one({"_id": query["_id"]}, {"$push": {"pins": pin}})
                return await real.update_one(query, update)

        changing = {records.USERS: ChangedUnderEveryWrite()}

        with pytest.raises(records.ListChangingError):
            await records.patch_pins(changing, user.id, _unchanged)

        assert ChangedUnderEveryWrite.writes > 1

    async def test_answers_false_for_a_user_there_is_not(self, database):
        assert not await records.patch_pins(database, uuid.uuid7(), _unchanged)


async def _unchanged(pins):
    return pins


class TestConvertingPinsKeptAsIds:
    """Pins were once kept as bare guide ids; each becomes a pin of that whole
    guide, once, at startup."""

    async def _stored_pins(self, database, user_id):
        found = await database[records.USERS].find_one({"_id": user_id})
        return found.get(models.PINS)

    async def _with_pins(self, database, *stored):
        user, _ = await _sign_in(database, _object_id())
        await database[records.USERS].update_one(
            {"_id": user.id}, {"$set": {models.PINS: list(stored)}}
        )
        return user

    async def test_guide_ids_become_pins_of_whole_guides_in_their_order(self, database):
        first, second = uuid.uuid7(), uuid.uuid7()
        user = await self._with_pins(database, first, second)

        await records.convert_pins_kept_as_ids(database)

        assert await self._stored_pins(database, user.id) == [
            models.Pin(document_id=first).to_document(),
            models.Pin(document_id=second).to_document(),
        ]

    async def test_pins_kept_as_pins_stay_as_they_are(self, database):
        section = models.Pin(
            document_id=uuid.uuid7(), section_id=uuid.uuid7(), heading="1 One"
        ).to_document()
        user = await self._with_pins(database, section)

        await records.convert_pins_kept_as_ids(database)
        await records.convert_pins_kept_as_ids(database)

        assert await self._stored_pins(database, user.id) == [section]

    async def test_a_guide_id_among_pins_becomes_a_pin_and_the_rest_stay(
        self, database
    ):
        guide = uuid.uuid7()
        section = models.Pin(
            document_id=uuid.uuid7(), section_id=uuid.uuid7(), heading="1 One"
        ).to_document()
        user = await self._with_pins(database, section, guide)

        await records.convert_pins_kept_as_ids(database)

        assert await self._stored_pins(database, user.id) == [
            section,
            models.Pin(document_id=guide).to_document(),
        ]

    async def test_a_user_with_no_pins_is_left_with_none(self, database):
        user, _ = await _sign_in(database, _object_id())

        await records.convert_pins_kept_as_ids(database)

        assert await records.find(database, user.id) == user


class TestRecordingAGuideAsOpened:
    async def _opened(self, database, user, *guides):
        start = dt.datetime.now(tz=dt.UTC)
        for n, guide in enumerate(guides):
            assert await records.record_opened(
                database, user.id, guide, start + dt.timedelta(seconds=n)
            )
        found = await records.find(database, user.id)
        return [opened.document_id for opened in found.recently_opened]

    async def test_puts_the_guide_first_with_when_it_was_opened(self, database):
        user, _ = await _sign_in(database, _object_id())
        first, second = uuid.uuid7(), uuid.uuid7()
        now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)

        await records.record_opened(database, user.id, first, now)
        await records.record_opened(
            database, user.id, second, now + dt.timedelta(seconds=1)
        )

        found = await records.find(database, user.id)
        assert [o.document_id for o in found.recently_opened] == [second, first]
        assert found.recently_opened[0].opened_at.replace(
            tzinfo=dt.UTC
        ) == now + dt.timedelta(seconds=1)

    async def test_keeps_one_entry_per_guide_moving_a_guide_opened_again_to_the_front(
        self, database
    ):
        user, _ = await _sign_in(database, _object_id())
        a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

        assert await self._opened(database, user, a, b, c, a) == [a, c, b]

    async def test_keeps_the_five_opened_most_recently(self, database):
        user, _ = await _sign_in(database, _object_id())
        guides = [uuid.uuid7() for _ in range(7)]

        assert await self._opened(database, user, *guides) == guides[:1:-1]

    async def test_an_opening_that_arrives_late_takes_its_place_by_its_time(
        self, database
    ):
        user, _ = await _sign_in(database, _object_id())
        guides = [uuid.uuid7() for _ in range(6)]
        start = dt.datetime.now(tz=dt.UTC)
        opened_at = {
            guide: start + dt.timedelta(seconds=n) for n, guide in enumerate(guides)
        }

        for guide in [*guides[1:], guides[0]]:
            await records.record_opened(database, user.id, guide, opened_at[guide])

        found = await records.find(database, user.id)
        assert [o.document_id for o in found.recently_opened] == guides[:0:-1]

    async def test_an_older_opening_arriving_late_leaves_the_guides_newer_time(
        self, database
    ):
        user, _ = await _sign_in(database, _object_id())
        guide = uuid.uuid7()
        now = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)

        await records.record_opened(database, user.id, guide, now)
        await records.record_opened(
            database, user.id, guide, now - dt.timedelta(seconds=1)
        )

        found = await records.find(database, user.id)
        assert [o.opened_at.replace(tzinfo=dt.UTC) for o in found.recently_opened] == [
            now
        ]

    async def test_stores_only_what_the_list_shows(self, database):
        user, _ = await _sign_in(database, _object_id())
        guides = [uuid.uuid7() for _ in range(7)]

        await self._opened(database, user, *guides, guides[-1], guides[-1])

        stored = await database[records.USERS].find_one({"_id": user.id})
        assert len(stored[models.RECENTLY_OPENED]) == 5

    async def test_many_opened_at_once_keep_at_most_five_and_no_guide_twice(
        self, database
    ):
        user, _ = await _sign_in(database, _object_id())
        guides = [uuid.uuid7() for _ in range(4)] * 3
        now = dt.datetime.now(tz=dt.UTC)

        await asyncio.gather(
            *(records.record_opened(database, user.id, g, now) for g in guides)
        )

        found = await records.find(database, user.id)
        opened = [o.document_id for o in found.recently_opened]
        assert len(opened) == len(set(opened)) == 4

    async def test_answers_false_for_a_user_there_is_not(self, database):
        now = dt.datetime.now(tz=dt.UTC)

        assert not await records.record_opened(
            database, uuid.uuid7(), uuid.uuid7(), now
        )

    async def test_a_user_who_has_opened_nothing_has_nothing_recently_opened(
        self, database
    ):
        user, _ = await _sign_in(database, _object_id())

        assert user.recently_opened == ()


class TestIndexes:
    async def test_the_identity_is_unique(self, database):
        indexes = await database[records.USERS].index_information()

        unique = [
            [name for name, _ in index["key"]]
            for index in indexes.values()
            if index.get("unique")
        ]

        assert ["identity.tenantId", "identity.objectId"] in unique
