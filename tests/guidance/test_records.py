"""Recording documents and versions in a real MongoDB (testcontainers).

What these are for is the one thing the router's stand-in collections cannot show
faithfully: which clashes Mongo reports, and which of them is an earlier attempt's
work to keep rather than a fault to raise.
"""

from __future__ import annotations

import uuid

import pymongo.errors
import pytest

from app.guidance import records


@pytest.fixture
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    return mongo_database


def _source(upload_id: str) -> dict[str, str]:
    return {"uploadId": upload_id, "fileId": str(uuid.uuid4())}


class TestCreatingADocument:
    async def test_a_document_already_recorded_is_answered_as_it_stands(self, database):
        document_id, upload_id = str(uuid.uuid4()), str(uuid.uuid4())
        first = await records.create(
            database,
            document_id,
            metadata={"title": "first"},
            source=_source(upload_id),
        )

        again = await records.create(
            database,
            document_id,
            metadata={"title": "again"},
            source=_source(upload_id),
        )

        assert again["metadata"] == {"title": "first"}
        assert again["_id"] == first["_id"]
        assert (
            await database[records.DOCUMENTS].count_documents({"_id": document_id}) == 1
        )

    async def test_an_upload_already_made_into_another_document_is_raised(
        self, database
    ):
        """A clash on the upload, not on the id: there is no record under this id to
        answer, so it is not an earlier attempt at this document."""
        upload_id = str(uuid.uuid4())
        await records.create(
            database, str(uuid.uuid4()), metadata={}, source=_source(upload_id)
        )
        another_id, same_upload = str(uuid.uuid4()), _source(upload_id)

        with pytest.raises(pymongo.errors.DuplicateKeyError):
            await records.create(database, another_id, metadata={}, source=same_upload)


class TestCreatingAVersion:
    async def test_a_version_already_recorded_is_answered_as_it_stands(self, database):
        document_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
        await records.create_version(
            database, version_id, document_id=document_id, content_url="s3://d/first"
        )

        again = await records.create_version(
            database, version_id, document_id=document_id, content_url="s3://d/again"
        )

        assert again["contentUrl"] == "s3://d/first"
        assert (
            await database[records.VERSIONS].count_documents({"_id": version_id}) == 1
        )

    async def test_a_version_id_taken_by_another_document_is_raised(self, database):
        version_id = str(uuid.uuid4())
        await records.create_version(
            database, version_id, document_id=str(uuid.uuid4()), content_url="s3://d/x"
        )
        another_document_id = str(uuid.uuid4())

        with pytest.raises(records.MisfiledVersionError):
            await records.create_version(
                database,
                version_id,
                document_id=another_document_id,
                content_url="s3://d/y",
            )
