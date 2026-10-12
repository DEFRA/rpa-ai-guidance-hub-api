"""Integration tests for `MongoStagingStore` using testcontainers MongoDB.

Exercises real MongoDB operations: TTL index creation, upsert & duplicate key handling
during claim, update mutations, and round-trip retrieval.
"""

from __future__ import annotations

import asyncio
import uuid
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


class TestReserveIds:
    """The ids a document made from a file is stored under: minted once, on the
    file's staging record, and the same for every attempt after."""

    async def _parsed(self, staging_store, file_id: str) -> None:
        await staging_store.claim(file_id, f"upload/{file_id}")
        await staging_store.mark_complete(
            file_id,
            parsing_models.MinimalDocumentInfo(
                title="Claims", version="1", last_modified=None
            ),
        )

    async def test_reserves_a_pair_of_uuids_on_a_parsed_file(
        self, staging_store, mongo_database
    ):
        file_id = f"reserve-{uuid.uuid4()}"
        await self._parsed(staging_store, file_id)

        staged = await staging_store.reserve_ids(file_id)

        assert staged is not None
        assert staged.document_id != staged.version_id
        raw = await mongo_database[store.COLLECTION_NAME].find_one({"_id": file_id})
        assert (raw["document_id"], raw["version_id"]) == (
            staged.document_id,
            staged.version_id,
        )

    async def test_the_reserved_ids_are_stored_as_native_time_ordered_uuids(
        self, staging_store, mongo_database
    ):
        file_id = f"reserve-{uuid.uuid4()}"
        await self._parsed(staging_store, file_id)

        await staging_store.reserve_ids(file_id)

        raw = await mongo_database[store.COLLECTION_NAME].find_one({"_id": file_id})
        assert isinstance(raw["document_id"], uuid.UUID)
        assert isinstance(raw["version_id"], uuid.UUID)
        assert (raw["document_id"].version, raw["version_id"].version) == (7, 7)

    async def test_reserving_again_gives_the_same_ids(self, staging_store):
        file_id = f"reserve-{uuid.uuid4()}"
        await self._parsed(staging_store, file_id)

        first = await staging_store.reserve_ids(file_id)
        second = await staging_store.reserve_ids(file_id)

        assert first is not None
        assert second is not None
        assert (second.document_id, second.version_id) == (
            first.document_id,
            first.version_id,
        )

    async def test_requests_racing_to_reserve_all_get_the_same_ids(self, staging_store):
        """A double submission: whichever lands first decides, and the rest read
        its ids rather than minting their own."""
        file_id = f"reserve-{uuid.uuid4()}"
        await self._parsed(staging_store, file_id)

        reserved = await asyncio.gather(
            *(staging_store.reserve_ids(file_id) for _ in range(10))
        )

        assert len({(s.document_id, s.version_id) for s in reserved}) == 1

    async def test_reserves_nothing_for_a_file_that_is_not_staged(self, staging_store):
        assert await staging_store.reserve_ids(f"never-{uuid.uuid4()}") is None

    @pytest.mark.parametrize("failed", [False, True])
    async def test_reserves_nothing_for_a_file_whose_parse_is_not_complete(
        self, staging_store, mongo_database, failed
    ):
        file_id = f"reserve-{uuid.uuid4()}"
        await staging_store.claim(file_id, f"upload/{file_id}")
        if failed:
            await staging_store.mark_failed(file_id, "not a .docx")

        assert await staging_store.reserve_ids(file_id) is None
        raw = await mongo_database[store.COLLECTION_NAME].find_one({"_id": file_id})
        assert "document_id" not in raw


class TestPromote:
    async def test_marks_the_file_as_promoted_and_keeps_its_record(self, staging_store):
        file_id = f"promote-{uuid.uuid4()}"
        await TestReserveIds()._parsed(staging_store, file_id)
        reserved = await staging_store.reserve_ids(file_id)
        before = datetime.now(UTC) - timedelta(seconds=1)

        await staging_store.promote(file_id)

        staged = await staging_store.get(file_id)
        assert staged is not None
        assert staged.promoted_at is not None
        assert staged.promoted_at >= before
        assert staged.document_id == reserved.document_id

    async def test_a_file_not_yet_promoted_has_no_promotion(self, staging_store):
        file_id = f"promote-{uuid.uuid4()}"
        await staging_store.claim(file_id, f"upload/{file_id}")

        assert (await staging_store.get(file_id)).promoted_at is None

    async def test_promoting_a_file_already_gone_is_not_an_error(self, staging_store):
        await staging_store.promote(f"never-{uuid.uuid4()}")
