from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

import pymongo.asynchronous.database
import pymongo.errors

from app.guidance.drafts import models as draft_models
from app.guidance.parsing import models as parsing_models

_COLLECTION = "guide_drafts"


class DraftStore(Protocol):
    async def claim(self, file_id: str, path: str) -> bool: ...

    async def mark_complete(
        self, file_id: str, info: parsing_models.MinimalDocumentInfo
    ) -> None: ...

    async def mark_failed(self, file_id: str, reason: str) -> None: ...

    async def get(self, file_id: str) -> draft_models.GuideDraft | None: ...


class MongoDraftStore:
    def __init__(
        self, db: pymongo.asynchronous.database.AsyncDatabase, retention_seconds: int
    ) -> None:
        self._collection = db[_COLLECTION]
        self._retention_seconds = retention_seconds

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("expires_at", expireAfterSeconds=0)

    async def claim(self, file_id: str, path: str) -> bool:
        now = datetime.now(UTC)

        try:
            await self._collection.update_one(
                {
                    "_id": file_id,
                    "parsing_status": draft_models.ParsingStatus.PENDING.value,
                },
                {
                    "$set": {
                        "parsing_status": draft_models.ParsingStatus.IN_PROGRESS.value,
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
                    "parsing_status": draft_models.ParsingStatus.COMPLETE.value,
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
                    "parsing_status": draft_models.ParsingStatus.FAILED.value,
                    "parse_error": reason,
                    "updated_at": datetime.now(UTC),
                }
            },
        )

    async def get(self, file_id: str) -> draft_models.GuideDraft | None:
        document = await self._collection.find_one({"_id": file_id})

        if document is None:
            return None

        return draft_models.GuideDraft.from_document(document)
