"""Recording documents and versions in a real MongoDB (testcontainers).

What these are for is the one thing the router's stand-in collections cannot show
faithfully: which clashes Mongo reports, and which of them is an earlier attempt's
work to keep rather than a fault to raise.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pymongo.errors
import pytest

from app.guidance import ids, models, records


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
