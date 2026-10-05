"""Creating a document from an upload, and reading one back.

`POST /guides` is what the front end's journey ends in: the .docx has been uploaded
and scanned, the author has described it, and this is where those two become one
thing. It converts the upload, stores the version it makes, and records what the
document is.

The upload is named by its file id, and everything else about it is read from its
staging record rather than taken from the caller: where cdp-uploader left it, whether
it parsed, and the ids the document is stored under. Those ids are reserved on the
staging record the first time, so a retried or repeated submission finishes the same
document rather than starting another. The writes go bottom up - the content, then
its version record, then the document record, which commits it - and each leaves in
place whatever an earlier attempt already wrote.

The route is still `/guides` because that is what the front end calls and what a
reader of these documents calls them. Underneath, the thing being stored is a
document with versions - see `records` for why the two words are not the same.

Converting is done in a worker thread. Parsing a real guidance document is a second
of arithmetic over a zip archive and the object store calls are blocking, so awaiting
it on the event loop would stop the service answering anything else meanwhile.
"""

from __future__ import annotations

import datetime as dt
from logging import getLogger
from typing import TYPE_CHECKING, Annotated, Any

import fastapi
import pydantic
from fastapi.concurrency import run_in_threadpool

from app import config
from app.common import mongo, s3
from app.guidance import records, service
from app.guidance.documents import store
from app.guidance.documents.staging import models as staging_models
from app.guidance.documents.staging import router as staging_router
from app.guidance.documents.staging import store as staging_store
from app.guidance.parsing.errors import DocumentParseError

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/guides", tags=["guides"])
logger = getLogger(__name__)

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]
S3Client = Annotated[Any, fastapi.Depends(s3.get_s3_client)]
Staging = Annotated[
    staging_store.StagingStore, fastapi.Depends(staging_router.get_staging_store)
]


def get_guidance_service(
    s3_client: S3Client,
) -> service.GuidanceService:
    return service.GuidanceService(s3_client=s3_client)


class Source(pydantic.BaseModel):
    """Which upload the document is, as the front end knows it.

    Only names it: where the file is, and whether it parsed, are read from its
    staging record, so the caller never says where to read from.
    """

    upload_id: str = pydantic.Field(alias="uploadId")
    file_id: str = pydantic.Field(
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


class Version(pydantic.BaseModel):
    """One conversion: where its Markdown went, and who made it.

    The content URL is the whole of where. The pictures are named by the file itself,
    in addresses relative to that URL, so anything holding it can reach them and a
    second field pointing at them would be free to disagree with the file.

    The title is a copy of what the stored document called itself when this version
    was made, so that a list of versions reads as something a person can follow.
    """

    id: str
    content_url: str = pydantic.Field(alias="contentUrl")
    title: str | None = None
    created_by: Person | None = pydantic.Field(default=None, alias="createdBy")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")

    model_config = pydantic.ConfigDict(populate_by_name=True)


class Document(pydantic.BaseModel):
    """A document as it is answered: what it is, where it came from, and every
    version of it there has been."""

    id: str
    metadata: dict[str, Any]
    source: Source
    versions: list[Version]


def _version(record: dict[str, Any]) -> Version:
    """One stored version as it is answered.

    Validated from a dict rather than constructed, because the record is already in
    the shape the wire uses and naming each field twice - once as it is stored, once
    as it is sent - is where the two drift apart.
    """
    return Version.model_validate(
        {
            "id": record["_id"],
            "contentUrl": record["contentUrl"],
            "title": record.get("title"),
            "createdBy": record.get("createdBy"),
            "createdAt": record["createdAt"],
        }
    )


def _answer(document: dict[str, Any], versions: list[dict[str, Any]]) -> Document:
    return Document(
        id=document["_id"],
        metadata=document["metadata"],
        source=Source.model_validate(document["source"]),
        versions=[_version(version) for version in versions],
    )


async def _answered(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: str
) -> Document:
    """A document and its versions, read back as they now stand."""
    document = await records.find(database, document_id)
    if document is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )

    return _answer(document, await records.versions_of(database, document_id))


@router.post("", status_code=fastapi.status.HTTP_201_CREATED)
async def create_document(
    new: NewDocument,
    database: Database,
    response: fastapi.Response,
    guidance: Annotated[service.GuidanceService, fastapi.Depends(get_guidance_service)],
    staging: Staging,
) -> Document:
    """Convert an upload and record the document it becomes.

    Converting the same upload twice answers the document it made the first time,
    rather than making a second one. The journey ahead of this can be submitted more
    than once - a refresh, a back button, a retried request - and every one of those
    means the same version of the same document: a finished document is answered as
    it is, and an unfinished one is finished under the ids its first attempt
    reserved.
    """
    already = await records.find_by_upload(database, new.source.upload_id)
    if already is not None:
        response.status_code = fastapi.status.HTTP_200_OK
        return await _answered(database, already["_id"])

    staged, document_id, version_id = await _reserved(staging, new.source)
    source_url = f"s3://{config.get_config().source_docs_s3_bucket}/{staged.path}"

    stored = await _converted(guidance, source_url, document_id, version_id)

    logger.info(
        "Converted file %s into document %s version %s: %d sections, %d images",
        staged.file_id,
        stored.document_id,
        stored.version_id,
        stored.sections,
        stored.images,
    )

    # Bottom up. The version is recorded only once its content is wholly stored, and
    # the document last, because writing it is what commits the document: until it
    # exists nothing can reach the version, and once it does everything beneath it
    # is there.
    version = await records.create_version(
        database,
        stored.version_id,
        document_id=stored.document_id,
        content_url=stored.content,
        title=stored.title,
        created_by=new.created_by.model_dump(by_alias=True) if new.created_by else None,
    )
    document = await records.create(
        database,
        stored.document_id,
        metadata=new.metadata,
        source=new.source.model_dump(by_alias=True),
    )
    response.headers["Location"] = f"/guides/{stored.document_id}"
    return _answer(document, [version])


@router.get("/{document_id}")
async def read_document(document_id: str, database: Database) -> Document:
    """One document: what it is, and every version of it there has been."""
    return await _answered(database, document_id)


@router.get(
    "/{document_id}/content", response_class=fastapi.responses.PlainTextResponse
)
async def read_content(
    document_id: str,
    database: Database,
    s3_client: S3Client,
) -> str:
    """The Markdown of a document's newest version, as it is stored.

    Answered as it was written rather than re-rendered. What it says about its
    pictures is `../assets/...`, which resolves against the URL this content was read
    from - so a caller that keeps that URL can reach them, and `store.resolved` is
    what turns one into an address.
    """
    version = await records.latest_version(database, document_id)
    if version is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )

    content = await run_in_threadpool(
        store.read, version["contentUrl"], s3_client=s3_client
    )
    if content is None:
        # The record points at something that is not there: a version half-deleted,
        # or a bucket emptied under it. Not a 404, which would say the document does
        # not exist when it does.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"Document {document_id} is recorded but its content is missing",
        )

    return content.decode("utf-8")


async def _reserved(
    staging: staging_store.StagingStore, source: Source
) -> tuple[staging_models.StagedDocument, str, str]:
    """The upload's staging record, and the document and version ids reserved on it.

    Answers the caller's mistakes as their status codes: a file that was never
    staged (or whose record has expired), a file that is not part of the upload
    named with it, and a file whose parse has not completed.
    """
    staged = await staging.get(source.file_id)
    if staged is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No staged file {source.file_id}: never delivered, or expired",
        )

    # cdp-uploader keys a delivered file by the upload it belongs to, so a file id
    # paired with another upload's id is a mistake, and would record this file
    # against the wrong upload.
    if not staged.path.startswith(f"{source.upload_id}/"):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=f"File {source.file_id} is not part of upload {source.upload_id}",
        )

    if staged.parsing_status != staging_models.ParsingStatus.COMPLETE:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=f"File {source.file_id} is {staged.parsing_status}, not parsed",
        )

    reserved = await staging.reserve_ids(source.file_id)
    if reserved is None or reserved.document_id is None or reserved.version_id is None:
        # Expired between reading it and reserving on it.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No staged file {source.file_id}: never delivered, or expired",
        )

    return reserved, reserved.document_id, reserved.version_id


async def _converted(
    guidance: service.GuidanceService,
    source_url: str,
    document_id: str,
    version_id: str,
) -> service.StoredDocument:
    """Convert the upload, answering the caller's mistakes as their status codes."""
    try:
        return await run_in_threadpool(
            guidance.convert, source_url, document_id, version_id
        )
    except service.SourceRefusedError as refused:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail=str(refused)
        ) from refused
    except service.SourceMissingError as missing:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=str(missing)
        ) from missing
    except DocumentParseError as unreadable:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(unreadable),
        ) from unreadable
