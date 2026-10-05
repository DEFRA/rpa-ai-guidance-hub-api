from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ParsingStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class StagedDocument:
    file_id: str
    parsing_status: ParsingStatus
    path: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    version: str | None = None
    last_modified: datetime | None = None
    parse_error: str | None = None
    # The ids a document made from this file is stored under, reserved the first
    # time it is created and reused by every attempt after (see store.reserve_ids).
    document_id: str | None = None
    version_id: str | None = None
    # When the document made from this file was committed (see store.promote).
    promoted_at: datetime | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.file_id,
            "parsing_status": self.parsing_status.value,
            "path": self.path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "version": self.version,
            "last_modified": self.last_modified,
            "parse_error": self.parse_error,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "promoted_at": self.promoted_at,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> StagedDocument:
        created_at = document["created_at"]
        if isinstance(created_at, datetime) and created_at.tzinfo is None:
            created_at = created_at.replace(
                tzinfo=datetime.now().astimezone().tzinfo or UTC
            )
            created_at = created_at.astimezone(UTC)

        updated_at = document["updated_at"]
        if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
            updated_at = updated_at.replace(
                tzinfo=datetime.now().astimezone().tzinfo or UTC
            )
            updated_at = updated_at.astimezone(UTC)

        promoted_at = document.get("promoted_at", None)
        if isinstance(promoted_at, datetime) and promoted_at.tzinfo is None:
            promoted_at = promoted_at.replace(tzinfo=UTC)

        last_modified = document.get("last_modified", None)
        if isinstance(last_modified, datetime) and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=UTC)

        return cls(
            file_id=document["_id"],
            parsing_status=ParsingStatus(document["parsing_status"]),
            path=document["path"],
            created_at=created_at,
            updated_at=updated_at,
            title=document.get("title", None),
            version=document.get("version", None),
            last_modified=last_modified,
            parse_error=document.get("parse_error", None),
            document_id=document.get("document_id", None),
            version_id=document.get("version_id", None),
            promoted_at=promoted_at,
        )
