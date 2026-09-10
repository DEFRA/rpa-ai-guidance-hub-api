"""A fake `store.DraftStore` implementation for testing ports we own (§1.3).

Keeps the same PENDING -> IN_PROGRESS -> {COMPLETE,FAILED} invariant `claim`
promises against the real store: only the first `claim` for a given file_id
returns True. Keyed by file_id since one upload can carry several files, each
claimed and tracked independently.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.guidance.drafts.models import GuideDraft, ParsingStatus
from app.guidance.drafts.store import DraftStore
from app.guidance.parsing.models import MinimalDocumentInfo


class InMemoryDraftStore(DraftStore):
    def __init__(self) -> None:
        self.records: dict[str, GuideDraft] = {}

    async def claim(self, file_id: str, path: str) -> bool:
        existing = self.records.get(file_id)

        if existing is not None and existing.parsing_status != ParsingStatus.PENDING:
            return False

        now = datetime.now(UTC)
        if existing is None:
            existing = GuideDraft(
                file_id=file_id,
                parsing_status=ParsingStatus.PENDING,
                path=path,
                created_at=now,
                updated_at=now,
            )

        self.records[file_id] = replace(
            existing, parsing_status=ParsingStatus.IN_PROGRESS, updated_at=now
        )
        return True

    async def mark_complete(self, file_id: str, info: MinimalDocumentInfo) -> None:
        self.records[file_id] = replace(
            self.records[file_id],
            parsing_status=ParsingStatus.COMPLETE,
            title=info.title,
            version=info.version,
            last_modified=info.last_modified,
            updated_at=datetime.now(UTC),
        )

    async def mark_failed(self, file_id: str, reason: str) -> None:
        self.records[file_id] = replace(
            self.records[file_id],
            parsing_status=ParsingStatus.FAILED,
            parse_error=reason,
            updated_at=datetime.now(UTC),
        )

    async def get(self, file_id: str) -> GuideDraft | None:
        return self.records.get(file_id)
