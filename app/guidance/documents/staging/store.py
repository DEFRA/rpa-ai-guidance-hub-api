from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

import pymongo.asynchronous.database
import pymongo.errors

from app.guidance import ids
from app.guidance.documents.staging import models as staging_models
from app.guidance.parsing import models as parsing_models

COLLECTION_NAME = "document_staging"


class StagingStore(Protocol):
    async def claim(self, file_id: ids.FileId, path: str) -> bool: ...

    async def mark_complete(
        self, file_id: ids.FileId, info: parsing_models.MinimalDocumentInfo
    ) -> None: ...

    async def mark_failed(self, file_id: ids.FileId, reason: str) -> None: ...

    async def get(
        self, file_id: ids.FileId
    ) -> staging_models.StagedDocument | None: ...

    async def reserve_ids(
        self, file_id: ids.FileId
    ) -> staging_models.StagedDocument | None: ...

    async def promote(self, file_id: ids.FileId) -> None: ...


class MongoStagingStore:
    def __init__(
        self, db: pymongo.asynchronous.database.AsyncDatabase, retention_seconds: int
    ) -> None:
        self._collection = db[COLLECTION_NAME]
        self._retention_seconds = retention_seconds

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("expires_at", expireAfterSeconds=0)

    async def claim(self, file_id: ids.FileId, path: str) -> bool:
        now = datetime.now(UTC)

        try:
            await self._collection.update_one(
                {
                    "_id": file_id,
                    "parsing_status": staging_models.ParsingStatus.PENDING.value,
                },
                {
                    "$set": {
                        "parsing_status": staging_models.ParsingStatus.IN_PROGRESS.value,
                        "updated_at": now,
                        "expires_at": now + timedelta(seconds=self._retention_seconds),
                    },
                    "$setOnInsert": {"path": path, "created_at": now},
                },
                upsert=True,
            )
        except pymongo.errors.DuplicateKeyError:
            return False

        return True

    async def mark_complete(
        self, file_id: ids.FileId, info: parsing_models.MinimalDocumentInfo
    ) -> None:
        await self._collection.update_one(
            {"_id": file_id},
            {
                "$set": {
                    "parsing_status": staging_models.ParsingStatus.COMPLETE.value,
                    "title": info.title,
                    "version": info.version,
                    "last_modified": info.last_modified,
                    "updated_at": datetime.now(UTC),
                }
            },
        )

    async def mark_failed(self, file_id: ids.FileId, reason: str) -> None:
        await self._collection.update_one(
            {"_id": file_id},
            {
                "$set": {
                    "parsing_status": staging_models.ParsingStatus.FAILED.value,
                    "parse_error": reason,
                    "updated_at": datetime.now(UTC),
                }
            },
        )

    async def get(self, file_id: ids.FileId) -> staging_models.StagedDocument | None:
        document = await self._collection.find_one({"_id": file_id})

        if document is None:
            return None

        return staging_models.StagedDocument.from_document(document)

    async def reserve_ids(
        self, file_id: ids.FileId
    ) -> staging_models.StagedDocument | None:
        """Reserve the ids a document made from this file is stored under.

        The first call for a parsed file mints them; every later call, including one
        racing it, gets the same pair back. Set only where none are set yet, in one
        update, so whichever request lands first decides and the rest read its ids:
        that is what lets a retried or repeated submission finish one document
        rather than start another.

        Returns:
            The staged document with its ids, or None if the file is not staged (or
            has expired) or its parse is not complete.
        """
        await self._collection.update_one(
            {
                "_id": file_id,
                "parsing_status": staging_models.ParsingStatus.COMPLETE.value,
                "document_id": {"$exists": False},
            },
            {
                "$set": {
                    "document_id": ids.new_document_id(),
                    "version_id": ids.new_version_id(),
                    "updated_at": datetime.now(UTC),
                }
            },
        )

        staged = await self.get(file_id)
        if (
            staged is None
            or staged.parsing_status != staging_models.ParsingStatus.COMPLETE
            or staged.document_id is None
        ):
            return None

        return staged

    async def promote(self, file_id: ids.FileId) -> None:
        """Record that the document made from this file has been committed.

        The file has been promoted from staging to a document under the ids reserved
        on this record. Marked rather than removed: the record still answers for the
        upload until it expires, and "ids reserved but not promoted" is what an
        attempt that never committed looks like. Promoting a file whose record has
        already gone is not an error.
        """
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": file_id},
            {"$set": {"promoted_at": now, "updated_at": now}},
        )
