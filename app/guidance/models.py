"""What the guidance service works with: plain values, with no HTTP in them.

`schemas` converts these to and from what goes over the wire, and each model converts
itself to and from the record Mongo stores - `to_document` and `from_document` - so
the names a record is stored under are written down once, here.

A document's source and a version's creator are held flat, as fields of the model,
and nested only as stored: they are values from somewhere else (cdp-uploader, the
auth provider) with no behaviour of their own here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.guidance.ids import DocumentId, FileId, UploadId, VersionId

# The document a version belongs to: the many end of the one-to-many.
DOCUMENT_ID = "documentId"


@dataclass(frozen=True)
class Document:
    """What a document is to the people using it, and the upload it came from.

    `metadata` is what the journey collected, uninterpreted - see `records`.
    """

    id: DocumentId
    upload_id: UploadId
    file_id: FileId
    metadata: dict[str, Any]
    created_at: dt.datetime
    filename: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            "metadata": self.metadata,
            "source": {
                "uploadId": self.upload_id,
                "fileId": self.file_id,
                "filename": self.filename,
            },
            "createdAt": self.created_at,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Document:
        source = document["source"]
        return cls(
            id=document["_id"],
            upload_id=source["uploadId"],
            file_id=source["fileId"],
            filename=source.get("filename"),
            metadata=document["metadata"],
            created_at=document["createdAt"],
        )


@dataclass(frozen=True)
class Version:
    """One conversion of a document: where its Markdown went, and who made it.

    The creator is who was signed in, not who owns the document - see `records`.
    """

    id: VersionId
    document_id: DocumentId
    content_url: str
    created_at: dt.datetime
    title: str | None = None
    created_by_id: str | None = None
    created_by_display_name: str | None = None

    def to_document(self) -> dict[str, Any]:
        created_by = (
            None
            if self.created_by_id is None
            else {"id": self.created_by_id, "displayName": self.created_by_display_name}
        )
        return {
            "_id": self.id,
            DOCUMENT_ID: self.document_id,
            "contentUrl": self.content_url,
            "title": self.title,
            "createdBy": created_by,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Version:
        created_by = document.get("createdBy") or {}
        return cls(
            id=document["_id"],
            document_id=document[DOCUMENT_ID],
            content_url=document["contentUrl"],
            title=document.get("title"),
            created_by_id=created_by.get("id"),
            created_by_display_name=created_by.get("displayName"),
            created_at=document["createdAt"],
        )


@dataclass(frozen=True)
class StoredDocument:
    """Where a converted guide went, and what it turned out to be."""

    document_id: DocumentId
    version_id: VersionId
    content: str
    title: str
    sections: int
    images: int


@dataclass(frozen=True)
class ConversionJob:
    """One upload to convert and save, as a queue would carry it: everything the work
    needs, so nothing has to be asked of the request that started it, which has
    already been answered by the time the work runs."""

    file_id: FileId
    source_url: str
    document_id: DocumentId
    version_id: VersionId
    upload_id: UploadId
    metadata: dict[str, Any]
    filename: str | None = None
    created_by_id: str | None = None
    created_by_display_name: str | None = None

    def document(self, created_at: dt.datetime) -> Document:
        """The document this job makes."""
        return Document(
            id=self.document_id,
            upload_id=self.upload_id,
            file_id=self.file_id,
            filename=self.filename,
            metadata=self.metadata,
            created_at=created_at,
        )

    def version(self, stored: StoredDocument, created_at: dt.datetime) -> Version:
        """The version this job makes, once its content is stored."""
        return Version(
            id=stored.version_id,
            document_id=stored.document_id,
            content_url=stored.content,
            title=stored.title,
            created_by_id=self.created_by_id,
            created_by_display_name=self.created_by_display_name,
            created_at=created_at,
        )
