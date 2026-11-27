"""Recording documents and versions in a real MongoDB (testcontainers).

What these are for is the one thing the router's stand-in collections cannot show
faithfully: which clashes Mongo reports, and which of them is an earlier attempt's
work to keep rather than a fault to raise.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

import pymongo.errors
import pytest

from app.guidance import ids, models, records, source_links


@pytest.fixture
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    return mongo_database


def _document(
    document_id: str, upload_id: str, metadata: dict[str, str] | None = None
) -> models.Document:
    return models.Document(
        id=document_id,
        upload_id=upload_id,
        file_id=str(uuid.uuid4()),
        metadata=metadata or {},
        created_at=dt.datetime.now(tz=dt.UTC),
    )


def _version(version_id: str, document_id: str, content_url: str) -> models.Version:
    return models.Version(
        id=version_id,
        document_id=document_id,
        content_url=content_url,
        created_at=dt.datetime.now(tz=dt.UTC),
    )


class TestCreatingADocument:
    async def test_a_document_already_recorded_is_answered_as_it_stands(self, database):
        document_id, upload_id = str(uuid.uuid4()), str(uuid.uuid4())
        first = await records.create(
            database, _document(document_id, upload_id, {"title": "first"})
        )

        again = await records.create(
            database, _document(document_id, upload_id, {"title": "again"})
        )

        assert again.metadata == {"title": "first"}
        assert again.id == first.id
        assert (
            await database[records.DOCUMENTS].count_documents({"_id": document_id}) == 1
        )

    async def test_an_upload_already_made_into_another_document_is_raised(
        self, database
    ):
        """A clash on the upload, not on the id: there is no record under this id to
        answer, so it is not an earlier attempt at this document."""
        upload_id = str(uuid.uuid4())
        await records.create(database, _document(str(uuid.uuid4()), upload_id))
        another = _document(str(uuid.uuid4()), upload_id)

        with pytest.raises(pymongo.errors.DuplicateKeyError):
            await records.create(database, another)


class TestCreatingAVersion:
    async def test_a_version_already_recorded_is_answered_as_it_stands(self, database):
        document_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
        await records.create_version(
            database, _version(version_id, document_id, "s3://d/first")
        )

        again = await records.create_version(
            database, _version(version_id, document_id, "s3://d/again")
        )

        assert again.content_url == "s3://d/first"
        assert (
            await database[records.VERSIONS].count_documents({"_id": version_id}) == 1
        )

    async def test_a_version_id_taken_by_another_document_is_raised(self, database):
        version_id = str(uuid.uuid4())
        await records.create_version(
            database, _version(version_id, str(uuid.uuid4()), "s3://d/x")
        )
        misfiled = _version(version_id, str(uuid.uuid4()), "s3://d/y")

        with pytest.raises(records.MisfiledVersionError):
            await records.create_version(database, misfiled)

    async def test_a_second_version_following_the_same_one_is_refused(self, database):
        """Each was matched with the version it follows, so the second to be written
        would carry the ids of a version that is no longer the latest."""
        document_id, first = str(uuid.uuid4()), str(uuid.uuid4())
        await records.create_version(database, _version(first, document_id, "s3://d/1"))
        await records.create_version(
            database,
            dataclasses.replace(
                _version(str(uuid.uuid4()), document_id, "s3://d/2"), follows=first
            ),
        )
        overtaken = dataclasses.replace(
            _version(str(uuid.uuid4()), document_id, "s3://d/3"), follows=first
        )

        with pytest.raises(records.OvertakenVersionError):
            await records.create_version(database, overtaken)


class TestHowIdsAreStored:
    """As native uuids, which is what the models hold: an id becomes text only as an
    S3 prefix or on the wire."""

    async def test_a_documents_id_is_stored_as_a_uuid(self, database):
        document_id = ids.new_document_id()
        await records.create(database, _document(document_id, str(uuid.uuid4())))

        raw = await database[records.DOCUMENTS].find_one({"_id": document_id})

        assert isinstance(raw["_id"], uuid.UUID)

    async def test_a_versions_id_and_its_documents_are_stored_as_uuids(self, database):
        version_id, document_id = ids.new_version_id(), ids.new_document_id()
        await records.create_version(
            database, _version(version_id, document_id, "s3://d/x")
        )

        raw = await database[records.VERSIONS].find_one({"_id": version_id})

        assert isinstance(raw["_id"], uuid.UUID)
        assert isinstance(raw[records.DOCUMENT_ID], uuid.UUID)

    async def test_a_document_is_found_by_its_uuid_with_its_versions(self, database):
        document_id, version_id = ids.new_document_id(), ids.new_version_id()
        await records.create_version(
            database, _version(version_id, document_id, "s3://d/x")
        )
        await records.create(database, _document(document_id, str(uuid.uuid4())))

        found = await records.find(database, document_id)
        versions = await records.versions_of(database, document_id)

        assert found is not None
        assert found.id == document_id
        assert [version.id for version in versions] == [version_id]


class TestListingDocuments:
    async def test_documents_come_newest_first_after_the_cursor(self, database):
        await database[records.DOCUMENTS].delete_many({})
        first, second, third = (ids.new_document_id() for _ in range(3))
        for document_id in (first, second, third):
            await records.create(database, _document(document_id, str(uuid.uuid4())))

        everything = await records.list_documents(
            database, metadata={}, after=None, limit=10
        )
        older = await records.list_documents(
            database, metadata={}, after=second, limit=10
        )

        assert [d.id for d in everything] == [third, second, first]
        assert [d.id for d in older] == [first]

    async def test_a_limit_caps_the_documents_returned(self, database):
        await database[records.DOCUMENTS].delete_many({})
        for _ in range(3):
            await records.create(
                database, _document(ids.new_document_id(), str(uuid.uuid4()))
            )

        found = await records.list_documents(database, metadata={}, after=None, limit=2)

        assert len(found) == 2

    async def test_a_document_matches_when_any_value_of_a_key_is_asked_for(
        self, database
    ):
        await database[records.DOCUMENTS].delete_many({})
        both, other = ids.new_document_id(), ids.new_document_id()
        await records.create(
            database, _document(both, str(uuid.uuid4()), {"schemes": ["a", "b"]})
        )
        await records.create(
            database, _document(other, str(uuid.uuid4()), {"schemes": ["c"]})
        )

        found = await records.list_documents(
            database, metadata={"schemes": ["b", "z"]}, after=None, limit=10
        )

        assert [d.id for d in found] == [both]

    async def test_a_document_must_match_every_key_asked_for(self, database):
        await database[records.DOCUMENTS].delete_many({})
        wanted, wrong_system = ids.new_document_id(), ids.new_document_id()
        await records.create(
            database,
            _document(wanted, str(uuid.uuid4()), {"schemes": ["a"], "systems": ["x"]}),
        )
        await records.create(
            database,
            _document(
                wrong_system, str(uuid.uuid4()), {"schemes": ["a"], "systems": ["y"]}
            ),
        )

        found = await records.list_documents(
            database,
            metadata={"schemes": ["a"], "systems": ["x"]},
            after=None,
            limit=10,
        )

        assert [d.id for d in found] == [wanted]


class TestAGuidesDetailsAreItsLatestVersions:
    """Each version keeps the details given with it. A guide's details are its latest
    version's, or the guide's own where that version kept none (one saved before
    versions kept their own)."""

    @staticmethod
    async def _guide(database, own: dict, *given: dict | None) -> ids.DocumentId:
        """A guide recorded with `own` details, with a version for each of `given`,
        oldest first."""
        document_id = ids.new_document_id()
        await records.create(database, _document(document_id, str(uuid.uuid4()), own))
        start = dt.datetime.now(tz=dt.UTC)
        for n, details in enumerate(given):
            await records.create_version(
                database,
                models.Version(
                    id=ids.new_version_id(),
                    document_id=document_id,
                    content_url=f"s3://d/{n}",
                    created_at=start + dt.timedelta(seconds=n),
                    metadata=details,
                ),
            )
        return document_id

    async def test_a_guide_is_found_with_its_latest_versions_details(self, database):
        guide = await self._guide(
            database, {"owner": "first"}, {"owner": "first"}, {"owner": "second"}
        )

        found = await records.find(database, guide)

        assert found.metadata == {"owner": "second"}

    async def test_a_guide_whose_latest_version_kept_none_is_found_with_its_own(
        self, database
    ):
        guide = await self._guide(database, {"owner": "first"}, None)

        found = await records.find(database, guide)

        assert found.metadata == {"owner": "first"}

    async def test_a_guide_is_listed_and_filtered_by_its_latest_versions_details(
        self, database
    ):
        old, new = f"old {uuid.uuid4()}", f"new {uuid.uuid4()}"
        guide = await self._guide(
            database, {"schemes": [old]}, {"schemes": [old]}, {"schemes": [new]}
        )

        by_new = await records.list_documents(
            database, metadata={"schemes": [new]}, after=None, limit=10
        )
        by_old = await records.list_documents(
            database, metadata={"schemes": [old]}, after=None, limit=10
        )

        assert [d.id for d in by_new] == [guide]
        assert by_new[0].metadata == {"schemes": [new]}
        assert by_old == []

    async def test_a_guide_is_counted_by_its_latest_versions_details(self, database):
        guide = await self._guide(
            database, {"schemes": ["a"]}, {"schemes": ["a"]}, {"schemes": ["b"]}
        )

        counted = await records.count_values(
            database, metadata={}, keys=["schemes"], only=[guide]
        )

        assert counted == {"schemes": {"b": 1}}

    async def test_a_pinned_guide_matches_and_reads_by_its_latest_versions_details(
        self, database
    ):
        guide = await self._guide(
            database, {"audience": ["a"]}, {"audience": ["a"]}, {"audience": ["b"]}
        )

        shown = await records.matching(database, [guide], {"audience": ["b"]})
        found = await records.find_many(database, [guide])

        assert shown == {guide}
        assert found[guide].metadata == {"audience": ["b"]}


class TestLatestVersions:
    async def test_each_document_gets_its_newest_version_and_none_without(
        self, database
    ):
        with_two, with_none = ids.new_document_id(), ids.new_document_id()
        old = _version(ids.new_version_id(), with_two, "s3://d/old")
        new = models.Version(
            id=ids.new_version_id(),
            document_id=with_two,
            content_url="s3://d/new",
            created_at=old.created_at + dt.timedelta(seconds=1),
        )
        await records.create_version(database, old)
        await records.create_version(database, new)

        latest = await records.latest_versions_of(database, [with_two, with_none])

        assert {k: v.id for k, v in latest.items()} == {with_two: new.id}


class TestIndexes:
    async def test_filterable_metadata_is_indexed(self, database):
        indexes = await database[records.DOCUMENTS].index_information()

        keys = {name for index in indexes.values() for name, _ in index["key"]}

        assert {f"metadata.{key}" for key in models.FILTERABLE_METADATA} <= keys


SAVED = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.UTC)


def _canonical() -> str:
    """A canonical link to a Word document of its own, so that no other case's
    versions name it."""
    return f"https://example.sharepoint.com/teams/team1234/claims/{uuid.uuid4()}.docx"


async def _saved(
    database, guide: ids.DocumentId, minutes: int, canonical: str | None
) -> None:
    """A version of `guide` saved `minutes` after SAVED, from `canonical`, or from no
    source link."""
    await records.create_version(
        database,
        models.Version(
            id=ids.new_version_id(),
            document_id=guide,
            content_url="s3://docs/content.md",
            created_at=SAVED + dt.timedelta(minutes=minutes),
            source_link=(
                None
                if canonical is None
                else source_links.SourceLink(f"{canonical}?d=w1&web=1", canonical)
            ),
        ),
    )


class TestGuidesKnownBySourceLinks:
    async def test_a_guide_is_known_by_its_latest_versions_source_link(self, database):
        guide, form = ids.new_document_id(), _canonical()
        await _saved(database, guide, 0, form)

        known = await records.guides_known_by(database, [form, _canonical()])

        assert known == {form: guide}

    async def test_a_guide_is_known_by_an_earlier_versions_source_link_too(
        self, database
    ):
        guide, before, after = ids.new_document_id(), _canonical(), _canonical()
        await _saved(database, guide, 0, before)
        await _saved(database, guide, 1, after)

        known = await records.guides_known_by(database, [before, after])

        assert known == {before: guide, after: guide}

    async def test_a_guide_is_known_by_an_earlier_source_link_when_its_latest_has_none(
        self, database
    ):
        guide, form = ids.new_document_id(), _canonical()
        await _saved(database, guide, 0, form)
        await _saved(database, guide, 1, None)

        known = await records.guides_known_by(database, [form])

        assert known == {form: guide}

    async def test_a_link_names_the_guide_whose_latest_version_has_it(self, database):
        holds_it_now, held_it_later, form = (
            ids.new_document_id(),
            ids.new_document_id(),
            _canonical(),
        )
        await _saved(database, holds_it_now, 0, form)
        await _saved(database, held_it_later, 1, form)
        await _saved(database, held_it_later, 2, _canonical())

        known = await records.guides_known_by(database, [form])

        assert known == {form: holds_it_now}

    async def test_a_link_two_latest_versions_have_names_the_newer_ones_guide(
        self, database
    ):
        older, newer, form = ids.new_document_id(), ids.new_document_id(), _canonical()
        await _saved(database, newer, 1, form)
        await _saved(database, older, 0, form)

        known = await records.guides_known_by(database, [form])

        assert known == {form: newer}

    async def test_a_link_no_latest_version_has_names_the_guide_that_held_it_last(
        self, database
    ):
        held_it_first, held_it_last, form = (
            ids.new_document_id(),
            ids.new_document_id(),
            _canonical(),
        )
        await _saved(database, held_it_first, 0, form)
        await _saved(database, held_it_last, 1, form)
        await _saved(database, held_it_first, 2, _canonical())
        await _saved(database, held_it_last, 3, None)

        known = await records.guides_known_by(database, [form])

        assert known == {form: held_it_last}

    async def test_names_no_guide_for_no_links(self, database):
        assert await records.guides_known_by(database, []) == {}

    async def test_are_found_through_the_index_on_the_canonical_source_link(
        self, database
    ):
        form = _canonical()
        await _saved(database, ids.new_document_id(), 0, form)
        before = await _uses_of_the_source_link_index(database)

        await records.guides_known_by(database, [form])

        assert await _uses_of_the_source_link_index(database) > before


async def _uses_of_the_source_link_index(database) -> int:
    """How many operations have used the index on each version's canonical source
    link."""
    cursor = await database[records.VERSIONS].aggregate([{"$indexStats": {}}])
    async for index in cursor:
        if index["key"] == {"sourceLink.canonicalLink": 1}:
            return index["accesses"]["ops"]
    message = "No index on sourceLink.canonicalLink"
    raise AssertionError(message)
