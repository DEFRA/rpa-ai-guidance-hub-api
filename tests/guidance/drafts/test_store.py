"""Integration tests for `MongoDraftStore` using testcontainers MongoDB.

Exercises real MongoDB operations: TTL index creation, upsert & duplicate key handling
during claim, update mutations, and round-trip retrieval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.guidance.drafts.models import ParsingStatus
from app.guidance.drafts.store import MongoDraftStore
from app.guidance.parsing.models import MinimalDocumentInfo

RETENTION_SECONDS = 14400


@pytest.fixture
def store(mongo_database) -> MongoDraftStore:
    return MongoDraftStore(mongo_database, RETENTION_SECONDS)


class TestEnsureIndexes:
    async def test_creates_ttl_index_on_expires_at(self, store, mongo_database):
        await store.ensure_indexes()

        index_info = await mongo_database["guide_drafts"].index_information()

        assert "expires_at_1" in index_info
        assert index_info["expires_at_1"]["expireAfterSeconds"] == 0
        assert index_info["expires_at_1"]["key"] == [("expires_at", 1)]


class TestClaim:
    async def test_inserts_pending_draft_and_sets_in_progress_with_retention_ttl(
        self, store, mongo_database
    ):
        file_id = "test-file-1"
        s3_path = "scanned/test-file-1.docx"
        before = datetime.now(UTC) - timedelta(seconds=1)

        claimed = await store.claim(file_id, s3_path)

        assert claimed is True
        raw_doc = await mongo_database["guide_drafts"].find_one({"_id": file_id})
        assert raw_doc is not None
        assert raw_doc["_id"] == file_id
        assert raw_doc["path"] == s3_path
        assert raw_doc["parsing_status"] == ParsingStatus.IN_PROGRESS.value
        created_at = raw_doc["created_at"].replace(tzinfo=UTC)
        updated_at = raw_doc["updated_at"].replace(tzinfo=UTC)
        assert created_at >= before
        assert updated_at >= before
        # Retention TTL matches roughly now + 14400s
        expected_expiry = raw_doc["updated_at"] + timedelta(seconds=RETENTION_SECONDS)
        assert abs((raw_doc["expires_at"] - expected_expiry).total_seconds()) < 2

    async def test_returns_false_when_document_has_already_been_claimed(self, store):
        file_id = "test-file-duplicate"
        s3_path = "scanned/test-file.docx"

        first_claim = await store.claim(file_id, s3_path)
        second_claim = await store.claim(file_id, s3_path)

        assert first_claim is True
        assert second_claim is False


class TestMarkComplete:
    async def test_updates_status_and_metadata_in_mongo(self, store):
        file_id = "test-file-complete"

        await store.claim(file_id, "scanned/complete.docx")

        last_modified = datetime(2026, 3, 4, 10, 0, tzinfo=UTC)
        info = MinimalDocumentInfo(
            title="Guidance Scheme A",
            version="1.2",
            last_modified=last_modified,
        )

        await store.mark_complete(file_id, info)

        draft = await store.get(file_id)

        assert draft is not None
        assert draft.parsing_status == ParsingStatus.COMPLETE
        assert draft.title == "Guidance Scheme A"
        assert draft.version == "1.2"
        assert draft.last_modified == last_modified
        assert draft.parse_error is None


class TestMarkFailed:
    async def test_updates_status_and_error_reason_in_mongo(self, store):
        file_id = "test-file-failed"

        await store.claim(file_id, "scanned/corrupted.docx")

        await store.mark_failed(file_id, "Corrupted archive header")

        draft = await store.get(file_id)

        assert draft is not None
        assert draft.parsing_status == ParsingStatus.FAILED
        assert draft.parse_error == "Corrupted archive header"


class TestGet:
    async def test_reconstitutes_guide_draft_when_found(self, store):
        file_id = "test-file-get"
        await store.claim(file_id, "scanned/get.docx")

        draft = await store.get(file_id)

        assert draft is not None
        assert draft.file_id == file_id
        assert draft.path == "scanned/get.docx"
        assert draft.parsing_status == ParsingStatus.IN_PROGRESS

    async def test_returns_none_when_not_found(self, store):
        draft = await store.get("non-existent-id")

        assert draft is None
