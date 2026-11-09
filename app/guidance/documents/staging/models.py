from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from collections.abc import Mapping
from typing import Any

from app.guidance import ids


class ParsingStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


class SavingStatus(enum.StrEnum):
    """How far saving the document this file converts into has got.

    None on a record means no save has been started.
    """

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class UploadedDocument:
    """A file cdp-uploader has scanned and delivered: what it is called, and where."""

    file_id: ids.FileId
    s3_key: str


@dataclasses.dataclass(frozen=True)
class StagedDocument:
    file_id: ids.FileId
    parsing_status: ParsingStatus
    path: str
    created_at: dt.datetime
    updated_at: dt.datetime
    title: str | None = None
    version: str | None = None
    last_modified: dt.datetime | None = None
    parse_error: str | None = None
    # The ids a document made from this file is stored under, reserved the first
    # time it is created and reused by every attempt after (see store.reserve_ids).
    document_id: ids.DocumentId | None = None
    version_id: ids.VersionId | None = None
    # When the document made from this file was committed (see store.promote).
    promoted_at: dt.datetime | None = None
    # Saving that document: started once (see store.start_saving), counted in steps -
    # one write per picture and one for the Markdown - and finished or failed.
    saving_status: SavingStatus | None = None
    save_steps_completed: int | None = None
    save_steps_total: int | None = None
    save_error: str | None = None

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
            "saving_status": self.saving_status.value if self.saving_status else None,
            "save_steps_completed": self.save_steps_completed,
            "save_steps_total": self.save_steps_total,
            "save_error": self.save_error,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> StagedDocument:
        created_at = document["created_at"]
        if isinstance(created_at, dt.datetime) and created_at.tzinfo is None:
            created_at = created_at.replace(
                tzinfo=dt.datetime.now().astimezone().tzinfo or dt.UTC
            )
            created_at = created_at.astimezone(dt.UTC)

        updated_at = document["updated_at"]
        if isinstance(updated_at, dt.datetime) and updated_at.tzinfo is None:
            updated_at = updated_at.replace(
                tzinfo=dt.datetime.now().astimezone().tzinfo or dt.UTC
            )
            updated_at = updated_at.astimezone(dt.UTC)

        promoted_at = document.get("promoted_at", None)
        if isinstance(promoted_at, dt.datetime) and promoted_at.tzinfo is None:
            promoted_at = promoted_at.replace(tzinfo=dt.UTC)

        last_modified = document.get("last_modified", None)
        if isinstance(last_modified, dt.datetime) and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=dt.UTC)

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
            saving_status=(
                SavingStatus(document["saving_status"])
                if document.get("saving_status")
                else None
            ),
            save_steps_completed=document.get("save_steps_completed"),
            save_steps_total=document.get("save_steps_total"),
            save_error=document.get("save_error"),
        )
