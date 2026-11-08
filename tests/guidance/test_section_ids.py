"""Each section's id, kept with its version, in a real MongoDB (testcontainers).

A section's id is what anything pointing into a guide keeps, so it must be given
once and read back the same however often, or however many at once, ask.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.guidance import ids, models, records, section_ids

ANCHORS = ["guide", "1-claims", "1-1-pay"]


@pytest.fixture
async def version(mongo_database) -> ids.VersionId:
    """A version stored as every version on `main` is: with no section ids."""
    version_id = ids.new_version_id()
    await records.create_version(
        mongo_database,
        models.Version(
            id=version_id,
            document_id=ids.new_document_id(),
            content_url=f"s3://docs/guide/{version_id}/content.md",
            created_at=dt.datetime.now(tz=dt.UTC),
        ),
    )
    return version_id


class TestSectionIds:
    async def test_are_given_the_first_time_one_for_each_section_all_different(
        self, mongo_database, version
    ):
        given = await section_ids.of_version(mongo_database, version, ANCHORS)

        assert list(given) == ANCHORS
        assert {section_id.version for section_id in given.values()} == {7}
        assert len(set(given.values())) == len(ANCHORS)

    async def test_are_kept_with_the_version(self, mongo_database, version):
        given = await section_ids.of_version(mongo_database, version, ANCHORS)

        kept = await mongo_database[records.VERSIONS].find_one({"_id": version})

        assert kept["sectionIds"] == [
            {"anchor": anchor, "id": section_id} for anchor, section_id in given.items()
        ]

    async def test_are_the_same_every_time_after(self, mongo_database, version):
        first = await section_ids.of_version(mongo_database, version, ANCHORS)

        again = await section_ids.of_version(mongo_database, version, ANCHORS)

        assert again == first

    async def test_asked_for_at_once_are_the_ones_kept_first_for_both(
        self, mongo_database, version
    ):
        """The other request keeps its ids between this one finding none and
        writing its own, so this one must not write over them."""
        theirs: dict[str, ids.SectionId] = {}

        async def another_request_keeps_its_ids() -> None:
            theirs.update(
                await section_ids.of_version(mongo_database, version, ANCHORS)
            )

        overtaken = _OvertakenBeforeWriting(
            mongo_database, another_request_keeps_its_ids
        )

        mine = await section_ids.of_version(overtaken, version, ANCHORS)

        assert list(theirs) == ANCHORS
        assert mine == theirs

    async def test_are_none_for_a_version_not_there(self, mongo_database):
        missing = ids.new_version_id()

        assert await section_ids.of_version(mongo_database, missing, ANCHORS) == {}


class _OvertakenBeforeWriting:
    """The database as one request sees it when another request runs to the end in
    the moment before this one writes."""

    def __init__(self, database, other_request):
        self._database = database
        self._other_request = other_request

    def __getitem__(self, name):
        return _OvertakenCollection(self._database[name], self._other_request)


class _OvertakenCollection:
    def __init__(self, collection, other_request):
        self._collection = collection
        self._other_request = other_request

    async def find_one(self, *args, **kwargs):
        return await self._collection.find_one(*args, **kwargs)

    async def update_one(self, *args, **kwargs):
        await self._other_request()
        return await self._collection.update_one(*args, **kwargs)
