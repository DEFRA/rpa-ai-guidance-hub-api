"""Integration tests for `MongoStagingStore` using testcontainers MongoDB.

Exercises real MongoDB operations: TTL index creation, upsert & duplicate key handling
during claim, update mutations, and round-trip retrieval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.guidance.documents.staging import models, store
from app.guidance.parsing import models as parsing_models

RETENTION_SECONDS = 14400


@pytest.fixture
def staging_store(mongo_database) -> store.MongoStagingStore:
    return store.MongoStagingStore(mongo_database, RETENTION_SECONDS)


class TestEnsureIndexes:
    async def test_creates_ttl_index_on_expires_at(self, staging_store, mongo_database):
        await staging_store.ensure_indexes()

        index_info = await mongo_database[store.COLLECTION_NAME].index_information()

        assert "expires_at_1" in index_info
        assert index_info["expires_at_1"]["expireAfterSeconds"] == 0
        assert index_info["expires_at_1"]["key"] == [("expires_at", 1)]


class TestClaim:
    async def test_inserts_pending_document_and_sets_in_progress_with_retention_ttl(
        self, staging_store, mongo_database
    ):
        file_id = "test-file-1"
        s3_path = "scanned/test-file-1.docx"
        before = datetime.now(UTC) - timedelta(seconds=1)

        claimed = await staging_store.claim(file_id, s3_path)

        assert claimed is True
        raw_doc = await mongo_database[store.COLLECTION_NAME].find_one({"_id": file_id})
        assert raw_doc is not None
        assert raw_doc["_id"] == file_id
        assert raw_doc["path"] == s3_path
        assert raw_doc["parsing_status"] == models.ParsingStatus.IN_PROGRESS.value
        created_at = raw_doc["created_at"].replace(tzinfo=UTC)
        updated_at = raw_doc["updated_at"].replace(tzinfo=UTC)
        assert created_at >= before
        assert updated_at >= before
        # Retention TTL matches roughly now + 14400s
        expected_expiry = raw_doc["updated_at"] + timedelta(seconds=RETENTION_SECONDS)
        assert abs((raw_doc["expires_at"] - expected_expiry).total_seconds()) < 2

    async def test_returns_false_when_document_has_already_been_claimed(
        self, staging_store
    ):
        file_id = "test-file-duplicate"
        s3_path = "scanned/test-file.docx"

        first_claim = await staging_store.claim(file_id, s3_path)
        second_claim = await staging_store.claim(file_id, s3_path)

        assert first_claim is True
        assert second_claim is False


class TestMarkComplete:
    async def test_updates_status_and_metadata_in_mongo(self, staging_store):
        file_id = "test-file-complete"

        await staging_store.claim(file_id, "scanned/complete.docx")

        last_modified = datetime(2026, 3, 4, 10, 0, tzinfo=UTC)
        info = parsing_models.MinimalDocumentInfo(
            title="Guidance Scheme A",
            version="1.2",
            last_modified=last_modified,
        )

        await staging_store.mark_complete(file_id, info)

        staged_document = await staging_store.get(file_id)

        assert staged_document is not None
        assert staged_document.parsing_status == models.ParsingStatus.COMPLETE
        assert staged_document.title == "Guidance Scheme A"
        assert staged_document.version == "1.2"
        assert staged_document.last_modified == last_modified
        assert staged_document.parse_error is None


class TestMarkFailed:
    async def test_updates_status_and_error_reason_in_mongo(self, staging_store):
        file_id = "test-file-failed"

        await staging_store.claim(file_id, "scanned/corrupted.docx")

        await staging_store.mark_failed(file_id, "Corrupted archive header")

        staged_document = await staging_store.get(file_id)

        assert staged_document is not None
        assert staged_document.parsing_status == models.ParsingStatus.FAILED
        assert staged_document.parse_error == "Corrupted archive header"


class TestGet:
    async def test_reconstitutes_staged_document_when_found(self, staging_store):
        file_id = "test-file-get"
        await staging_store.claim(file_id, "scanned/get.docx")

        staged_document = await staging_store.get(file_id)

        assert staged_document is not None
        assert staged_document.file_id == file_id
        assert staged_document.path == "scanned/get.docx"
        assert staged_document.parsing_status == models.ParsingStatus.IN_PROGRESS

    async def test_returns_none_when_not_found(self, staging_store):
        staged_document = await staging_store.get("non-existent-id")

        assert staged_document is None
