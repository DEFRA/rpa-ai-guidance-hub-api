"""Creating a guide from an uploaded document, and reading one back.

`POST /guides` is what the front end's journey ends in: the document has been
uploaded and scanned, the author has described it, and this is where those two become
one thing. It converts the .docx, stores the guide, and records what the guide is.

Converting is done in a worker thread. Parsing a real guidance document is a second
of arithmetic over a zip archive and the object store calls are blocking, so awaiting
it on the event loop would stop the service answering anything else meanwhile.
"""

from __future__ import annotations

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


class NewGuide(pydantic.BaseModel):
    """A converted document and what its author said about it."""

    source: Source
    metadata: dict[str, Any]
    created_by: str | None = pydantic.Field(default=None, alias="createdBy")

    model_config = pydantic.ConfigDict(populate_by_name=True)


class Guide(pydantic.BaseModel):
    """A guide as it is answered: what it is, and where its content went."""

    id: str
    title: str | None = None
    content: str
    assets: str
    metadata: dict[str, Any]


def _answer(record: dict[str, Any]) -> Guide:
    return Guide(
        id=record["_id"],
        title=record["content"].get("title"),
        content=record["content"]["url"],
        assets=record["content"]["assets"],
        metadata=record["metadata"],
    )


@router.post("", status_code=fastapi.status.HTTP_201_CREATED)
async def create_guide(
    new: NewGuide, database: Database, response: fastapi.Response
) -> Guide:
    """Convert an uploaded document and record the guide it becomes.

    Converting the same upload twice answers the guide it made the first time,
    rather than making a second one. The journey ahead of this can be submitted more
    than once - a refresh, a back button, a retried request - and every one of those
    means the same guide.
    """
    already = await records.find_by_upload(database, new.source.upload_id)
    if already is not None:
        response.status_code = fastapi.status.HTTP_200_OK
        return _answer(already)

    try:
        stored = await run_in_threadpool(service.convert, new.source.url)
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

    logger.info(
        "Converted %s into guide %s: %d sections, %d images",
        new.source.url,
        stored.guide_id,
        stored.sections,
        stored.images,
    )

    record = await records.create(
        database,
        stored.guide_id,
        metadata=new.metadata,
        source=new.source.model_dump(by_alias=True),
        content={
            "url": stored.content,
            "assets": stored.assets,
            "title": stored.title,
        },
        created_by=new.created_by,
    )
    response.headers["Location"] = f"/guides/{stored.guide_id}"
    return _answer(record)


@router.get("/{guide_id}")
async def read_guide(guide_id: str, database: Database) -> Guide:
    """One guide: what it is, and where its content is."""
    record = await records.find(database, guide_id)
    if record is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {guide_id}",
        )

    return _answer(record)


@router.get("/{guide_id}/content", response_class=fastapi.responses.PlainTextResponse)
async def read_content(guide_id: str, database: Database) -> str:
    """A guide's Markdown, as it is stored.

    Answered as it was written rather than re-rendered: the stored file addresses
    its pictures absolutely, so what a reader gets here needs nothing else to make
    sense of it.
    """
    record = await records.find(database, guide_id)
    if record is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {guide_id}",
        )

    content = await run_in_threadpool(store.read, record["content"]["url"])
    if content is None:
        # The record points at something that is not there: a guide half-deleted, or
        # a bucket emptied under it. Not a 404 for the guide, which does exist.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"Guide {guide_id} is recorded but its content is missing",
        )

    return content.decode("utf-8")
