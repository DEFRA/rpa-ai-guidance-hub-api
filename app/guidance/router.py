"""Creating a document from an upload, and reading one back.

`POST /guides` is what the front end's journey ends in: the .docx has been uploaded
and scanned, the author has described it, and this is where those two become one
thing. It converts the upload, stores the version it makes, and records what the
document is.

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

from app.common import mongo
from app.guidance import records, service
from app.guidance.documents import store
from app.guidance.parsing.errors import DocumentParseError

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/guides", tags=["guides"])
logger = getLogger(__name__)

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]


class Source(pydantic.BaseModel):
    """Where cdp-uploader left the document, as the front end read it back."""

    upload_id: str = pydantic.Field(alias="uploadId")
    url: str = pydantic.Field(
        description="Where the document is, as cdp-uploader's status reports it"
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
    """One conversion: where its Markdown went, and who made it."""

    id: str
    content: str
    assets: str
    title: str | None = None
    created_by: Person | None = pydantic.Field(default=None, alias="createdBy")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")

    model_config = pydantic.ConfigDict(populate_by_name=True)


class Document(pydantic.BaseModel):
    """A document as it is answered: what it is, and every version of it."""

    id: str
    metadata: dict[str, Any]
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
            "content": record["content"]["url"],
            "assets": record["content"]["assets"],
            "title": record["content"].get("title"),
            "createdBy": record.get("createdBy"),
            "createdAt": record["createdAt"],
        }
    )


def _answer(document: dict[str, Any], versions: list[dict[str, Any]]) -> Document:
    return Document(
        id=document["_id"],
        metadata=document["metadata"],
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
    new: NewDocument, database: Database, response: fastapi.Response
) -> Document:
    """Convert an upload and record the document it becomes.

    Converting the same upload twice answers the document it made the first time,
    rather than making a second one. The journey ahead of this can be submitted more
    than once - a refresh, a back button, a retried request - and every one of those
    means the same version of the same document.
    """
    already = await records.find_version_by_upload(database, new.source.upload_id)
    if already is not None:
        response.status_code = fastapi.status.HTTP_200_OK
        return await _answered(database, already[records.DOCUMENT_ID])

    stored = await _converted(new.source.url)

    logger.info(
        "Converted %s into document %s version %s: %d sections, %d images",
        new.source.url,
        stored.document_id,
        stored.version_id,
        stored.sections,
        stored.images,
    )

    # The document first: a document with no versions is a conversion that failed
    # half way, which is recoverable and legible. A version pointing at a document
    # that was never written is neither.
    document = await records.create(database, stored.document_id, metadata=new.metadata)
    version = await records.create_version(
        database,
        stored.version_id,
        document_id=stored.document_id,
        source=new.source.model_dump(by_alias=True),
        content={
            "url": stored.content,
            "assets": stored.assets,
            "title": stored.title,
        },
        created_by=new.created_by.model_dump(by_alias=True) if new.created_by else None,
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
async def read_content(document_id: str, database: Database) -> str:
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

    content = await run_in_threadpool(store.read, version["content"]["url"])
    if content is None:
        # The record points at something that is not there: a version half-deleted,
        # or a bucket emptied under it. Not a 404, which would say the document does
        # not exist when it does.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"Document {document_id} is recorded but its content is missing",
        )

    return content.decode("utf-8")


async def _converted(source_url: str) -> service.StoredDocument:
    """Convert the upload, answering the caller's mistakes as their status codes."""
    try:
        return await run_in_threadpool(service.convert, source_url)
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
