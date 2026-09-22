"""A fake `store.StagingStore` implementation for testing ports we own (§1.3).

Keeps the same PENDING -> IN_PROGRESS -> {COMPLETE,FAILED} invariant `claim`
promises against the real store: only the first `claim` for a given file_id
returns True. Keyed by file_id since one upload can carry several files, each
claimed and tracked independently.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.guidance.documents.staging import models, store
from app.guidance.parsing import models as parsing_models


class InMemoryStagingStore(store.StagingStore):
    def __init__(self) -> None:
        self.records: dict[str, models.StagedDocument] = {}

    async def claim(self, file_id: str, path: str) -> bool:
        existing = self.records.get(file_id)

        if (
            existing is not None
            and existing.parsing_status != models.ParsingStatus.PENDING
        ):
            return False

        now = datetime.now(UTC)
        if existing is None:
            existing = models.StagedDocument(
                file_id=file_id,
                parsing_status=models.ParsingStatus.PENDING,
                path=path,
                created_at=now,
                updated_at=now,
            )

        self.records[file_id] = replace(
            existing, parsing_status=models.ParsingStatus.IN_PROGRESS, updated_at=now
        )
        return True

    async def mark_complete(
        self, file_id: str, info: parsing_models.MinimalDocumentInfo
    ) -> None:
        self.records[file_id] = replace(
            self.records[file_id],
            parsing_status=models.ParsingStatus.COMPLETE,
            title=info.title,
            version=info.version,
            last_modified=info.last_modified,
            updated_at=datetime.now(UTC),
        )

    async def mark_failed(self, file_id: str, reason: str) -> None:
        self.records[file_id] = replace(
            self.records[file_id],
            parsing_status=models.ParsingStatus.FAILED,
            parse_error=reason,
            updated_at=datetime.now(UTC),
        )

    async def get(self, file_id: str) -> models.StagedDocument | None:
        return self.records.get(file_id)
