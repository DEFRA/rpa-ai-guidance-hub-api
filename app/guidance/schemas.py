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


class Person(pydantic.BaseModel):
    """Someone the auth provider knows.

    The id is the machine identifier and is what anything should match on; the
    display name is carried beside it so that a listing can say who made a version
    without asking a directory. That name is a copy taken at the time and will go
    stale if the person is renamed, which is the price of not needing the directory.
    """

    id: str
    display_name: str | None = pydantic.Field(default=None, alias="displayName")


class NewDocument(pydantic.BaseModel):
    """An upload to convert, and what its author said about the document."""

    source: Source
    metadata: dict[str, Any]
    created_by: Person | None = pydantic.Field(default=None, alias="createdBy")

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
    """A document as it is answered: what it is, where it came from, when it was
    recorded, and every version of it there has been.

    `createdAt` is when the document was recorded, which is when its first version
    was: the moment it went live, since every upload does.
    """

    id: ids.DocumentId
    metadata: dict[str, Any]
    source: Source
    created_at: dt.datetime = pydantic.Field(alias="createdAt")
    versions: list[Version]

    @classmethod
    def from_model(
        cls, document: models.Document, versions: list[models.Version]
    ) -> Document:
        return cls(
            id=document.id,
            metadata=models.details_as_now(document.metadata),
            source=Source(
                uploadId=document.upload_id,
                fileId=document.file_id,
                filename=document.filename,
            ),
            createdAt=document.created_at,
            versions=[Version.from_model(version) for version in versions],
        )


class DocumentSummary(pydantic.BaseModel):
    """A document as listed, with its newest version, and when the document was
    recorded (as for `Document`)."""

    id: ids.DocumentId
    metadata: dict[str, Any]
    source: Source
    created_at: dt.datetime = pydantic.Field(alias="createdAt")
    latest_version: Version | None = pydantic.Field(alias="latestVersion")

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, summary: models.DocumentSummary) -> DocumentSummary:
        document = summary.document
        latest = summary.latest_version
        return cls(
            id=document.id,
            metadata=models.details_as_now(document.metadata),
            source=Source(
                uploadId=document.upload_id,
                fileId=document.file_id,
                filename=document.filename,
            ),
            createdAt=document.created_at,
            latestVersion=None if latest is None else Version.from_model(latest),
        )


class OptionCount(pydantic.BaseModel):
    """One value of a filter, labelled as the reference data labels it, and how many
    of the guides have it given the other filters chosen."""

    label: str | None = pydantic.Field(
        description="Null for the guides with none of the filter's values"
    )
    short_label: str | None = pydantic.Field(alias="shortLabel")
    count: int

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, option: models.OptionCount) -> OptionCount:
        return cls(
            label=option.label, shortLabel=option.short_label, count=option.count
        )


def counts_from_model(
    counts: dict[str, list[models.OptionCount]],
) -> dict[str, list[OptionCount]]:
    """Each filter's values with their counts, by the filter's name."""
    return {
        name: [OptionCount.from_model(option) for option in options]
        for name, options in counts.items()
    }


class DocumentPage(pydantic.BaseModel):
    """One page of documents; `nextCursor` is null on the last. `counts` has, for
    each filter by its name (`scheme`, `system`, `audience`), the values the guides
    have, each with how many, so a page can offer the filters from this one call."""

    items: list[DocumentSummary]
    next_cursor: ids.DocumentId | None = pydantic.Field(alias="nextCursor")
    counts: dict[str, list[OptionCount]]

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, page: models.DocumentPage) -> DocumentPage:
        return cls(
            items=[DocumentSummary.from_model(item) for item in page.items],
            nextCursor=page.next_cursor,
            counts=counts_from_model(page.counts),
        )
