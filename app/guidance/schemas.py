"""The shapes a document takes over HTTP: what a caller sends, and what it is answered.

Pydantic, because these are validated on the way in, written into the OpenAPI document,
and spelt in camelCase on the wire. The service itself works with `models`; these
convert to and from them, and know nothing of how anything is stored.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pydantic

from app.guidance import ids, models


class Source(pydantic.BaseModel):
    """Which upload the document is, as the front end knows it.

    Only names it: where the file is, and whether it parsed, are read from its
    staging record, so the caller never says where to read from.
    """

    upload_id: ids.UploadId = pydantic.Field(alias="uploadId")
    file_id: ids.FileId = pydantic.Field(
        alias="fileId", description="The file's id, as cdp-uploader's status reports it"
    )
    filename: str | None = None

    model_config = pydantic.ConfigDict(populate_by_name=True)


class Person(pydantic.BaseModel):
    """Someone the auth provider knows.

    The id is the machine identifier and is what anything should match on; the
    display name is carried beside it so that a listing can say who made a version
    without asking a directory. That name is a copy taken at the time and will go
    stale if the person is renamed, which is the price of not needing the directory.
    """

    id: str
    display_name: str | None = pydantic.Field(default=None, alias="displayName")

    model_config = pydantic.ConfigDict(populate_by_name=True)


class NewDocument(pydantic.BaseModel):
    """An upload to convert, and what its author said about the document."""

    source: Source
    metadata: dict[str, Any]
    created_by: Person | None = pydantic.Field(default=None, alias="createdBy")

    model_config = pydantic.ConfigDict(populate_by_name=True)

    def document_for(
        self, document_id: ids.DocumentId, created_at: dt.datetime
    ) -> models.Document:
        """The document this request makes, once it has an id."""
        return models.Document(
            id=document_id,
            upload_id=self.source.upload_id,
            file_id=self.source.file_id,
            filename=self.source.filename,
            metadata=self.metadata,
            created_at=created_at,
        )

    def version_for(
        self, stored: models.StoredDocument, created_at: dt.datetime
    ) -> models.Version:
        """The version this request makes, once its content is stored."""
        return models.Version(
            id=stored.version_id,
            document_id=stored.document_id,
            content_url=stored.content,
            title=stored.title,
            created_by_id=self.created_by.id if self.created_by else None,
            created_by_display_name=(
                self.created_by.display_name if self.created_by else None
            ),
            created_at=created_at,
        )


class Version(pydantic.BaseModel):
    """One conversion: where its Markdown went, and who made it.

    The content URL is the whole of where. The pictures are named by the file itself,
    in addresses relative to that URL, so anything holding it can reach them and a
    second field pointing at them would be free to disagree with the file.

    The title is a copy of what the stored document called itself when this version
    was made, so that a list of versions reads as something a person can follow.
    """

    id: ids.VersionId
    content_url: str = pydantic.Field(alias="contentUrl")
    title: str | None = None
    created_by: Person | None = pydantic.Field(default=None, alias="createdBy")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, version: models.Version) -> Version:
        created_by = (
            None
            if version.created_by_id is None
            else Person(
                id=version.created_by_id,
                displayName=version.created_by_display_name,
            )
        )
        return cls(
            id=version.id,
            contentUrl=version.content_url,
            title=version.title,
            createdBy=created_by,
            createdAt=version.created_at,
        )


class Document(pydantic.BaseModel):
    """A document as it is answered: what it is, where it came from, and every
    version of it there has been."""

    id: ids.DocumentId
    metadata: dict[str, Any]
    source: Source
    versions: list[Version]

    @classmethod
    def from_model(
        cls, document: models.Document, versions: list[models.Version]
    ) -> Document:
        return cls(
            id=document.id,
            metadata=document.metadata,
            source=Source(
                uploadId=document.upload_id,
                fileId=document.file_id,
                filename=document.filename,
            ),
            versions=[Version.from_model(version) for version in versions],
        )
