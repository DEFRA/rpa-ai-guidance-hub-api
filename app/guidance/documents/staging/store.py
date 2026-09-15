from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

import pymongo.asynchronous.database
import pymongo.errors

from app.guidance.documents.staging import models as staging_models
from app.guidance.parsing import models as parsing_models

COLLECTION_NAME = "document_staging"


class StagingStore(Protocol):
    async def claim(self, file_id: str, path: str) -> bool: ...

    async def mark_complete(
        self, file_id: str, info: parsing_models.MinimalDocumentInfo
    ) -> None: ...

    async def mark_failed(self, file_id: str, reason: str) -> None: ...

    async def get(self, file_id: str) -> staging_models.StagedDocument | None: ...


class MongoStagingStore:
    def __init__(
        self, db: pymongo.asynchronous.database.AsyncDatabase, retention_seconds: int
    ) -> None:
        self._collection = db[COLLECTION_NAME]
        self._retention_seconds = retention_seconds

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("expires_at", expireAfterSeconds=0)

    async def claim(self, file_id: str, path: str) -> bool:
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
        self, file_id: str, info: parsing_models.MinimalDocumentInfo
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

    async def mark_failed(self, file_id: str, reason: str) -> None:
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

    async def get(self, file_id: str) -> staging_models.StagedDocument | None:
        document = await self._collection.find_one({"_id": file_id})

        if document is None:
            return None

        return staging_models.StagedDocument.from_document(document)
