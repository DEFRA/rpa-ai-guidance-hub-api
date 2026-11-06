"""Creating a document from an upload, and reading one back.

`POST /guides` is what the front end's journey ends in: the .docx has been uploaded
and scanned, the author has described it, and this is where those two become one
thing. It sends the upload to be converted and answers at once (202), pointing at the
file's staging record, where the caller follows the save to the end - see
`conversion` for how the work is run off the queue.

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

Converting is done after the reply. A document with many pictures is one object store
write per picture, and a caller waiting on all of them would wait for as long as the
slowest save takes.
"""

from __future__ import annotations

import mimetypes
from logging import getLogger
from typing import TYPE_CHECKING, Annotated, Any, Literal

import fastapi
from fastapi.concurrency import run_in_threadpool

from app import config
from app.common import mongo, s3
from app.guidance import conversion, ids, records, schemas, service
from app.guidance.documents import store
from app.guidance.documents.staging import models as staging_models
from app.guidance.documents.staging import router as staging_router
from app.guidance.documents.staging import schemas as staging_schemas
from app.guidance.documents.staging import store as staging_store

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/guides", tags=["guides"])
logger = getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# A picture is named by a digest and an extension: no separators, so no way out of
# the assets directory.
ASSET_NAME = r"^[A-Za-z0-9]+\.[A-Za-z0-9]+$"

# Only these are served as assets, matching what the front end will pass on.
ALLOWED_ASSET_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]
S3Client = Annotated[Any, fastapi.Depends(s3.get_s3_client)]
Staging = Annotated[
    staging_store.StagingStore, fastapi.Depends(staging_router.get_staging_store)
]


async def _answered(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> schemas.Document:
    """A document and its versions, read back as they now stand."""
    document = await records.find(database, document_id)
    if document is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )

    versions = await records.versions_of(database, document_id)
    return schemas.Document.from_model(document, versions)


def get_conversion_submitter(
    request: fastapi.Request,
) -> conversion.ConversionSubmitter:
    """What the app started with: the queue, or a refusal where none is configured."""
    submitter: conversion.ConversionSubmitter = request.app.state.conversion_submitter
    return submitter


@router.post(
    "",
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses={
        fastapi.status.HTTP_200_OK: {
            "model": schemas.Document,
            "description": "The upload was converted already: its document",
        },
        fastapi.status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The conversion could not be started; try again",
        },
    },
)
async def create_document(
    new: schemas.NewDocument,
    database: Database,
    response: fastapi.Response,
    staging: Staging,
    submitter: Annotated[
        conversion.ConversionSubmitter, fastapi.Depends(get_conversion_submitter)
    ],
) -> staging_schemas.StagedDocumentResponse | schemas.Document:
    """Start converting an upload into a document, and answer where to follow it.

    The answer is the file's staging record, also named by `Location`: it says how
    far the save has got, and once it is complete, the document it made.

    Submitting the same upload again never starts a second save. The journey ahead of
    this can be submitted more than once - a refresh, a back button, a retried request
    - and every one of those means the same version of the same document: a finished
    document is answered as it is (200), a save under way is answered as it stands,
    and a save that failed is started again under the ids its first attempt reserved.

    A job that cannot be sent is recorded as a failed save and answered 503, so the
    caller does not follow a save that will never come.
    """
    already = await records.find_by_upload(database, new.source.upload_id)
    if already is not None:
        response.status_code = fastapi.status.HTTP_200_OK
        return await _answered(database, already.id)

    staged, document_id, version_id = await _reserved(staging, new.source)

    if await staging.start_saving(staged.file_id):
        source_url = f"s3://{config.get_config().source_docs_s3_bucket}/{staged.path}"
        await _sent(
            submitter,
            staging,
            conversion.ConversionJob(
                file_id=staged.file_id,
                source_url=source_url,
                document_id=document_id,
                version_id=version_id,
                request=new,
            ),
        )

    response.headers["Location"] = f"/guides/staging/{staged.file_id}"
    saving = await staging.get(staged.file_id)
    return staging_schemas.StagedDocumentResponse.from_staged_document(saving or staged)


@router.get("")
async def list_documents(
    database: Database,
    limit: Annotated[int, fastapi.Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        ids.DocumentId | None,
        fastapi.Query(description="The `nextCursor` of the page before this one"),
    ] = None,
    scheme: Annotated[list[str] | None, fastapi.Query()] = None,
    system: Annotated[list[str] | None, fastapi.Query()] = None,
    audience: Annotated[list[str] | None, fastapi.Query()] = None,
    guidance_type: Annotated[list[str] | None, fastapi.Query()] = None,
    no_scheme: Annotated[
        bool, fastapi.Query(description="The guides specific to no scheme too")
    ] = False,
    sort: Annotated[
        Literal["updated-newest", "updated-oldest"],
        fastapi.Query(description="By when each guide last changed"),
    ] = "updated-newest",
) -> schemas.DocumentPage:
    """The documents there are, a page at a time, in order of when each last
    changed, with each filter's values and how many guides have each.

    Filters take values from the reference data and can be repeated. `no_scheme`
    chooses the guides specific to no scheme, beside any `scheme`s chosen. Follow
    `nextCursor` for the next page; it is null on the last.
    """
    filters = service.chosen_filters(
        {
            "scheme": scheme,
            "system": system,
            "audience": audience,
            "guidance_type": guidance_type,
        },
        no_scheme=no_scheme,
    )
    try:
        page = await service.list_page(
            database, filters=filters, after=cursor, limit=limit, sort=sort
        )
    except (service.UnknownFilterValueError, records.UnknownCursorError) as refused:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(refused),
        ) from refused

    return schemas.DocumentPage.from_model(page)


@router.get("/{document_id}")
async def read_document(
    document_id: ids.DocumentId, database: Database
) -> schemas.Document:
    """One document: what it is, and every version of it there has been."""
    return await _answered(database, document_id)


@router.get(
    "/{document_id}/content", response_class=fastapi.responses.PlainTextResponse
)
async def read_content(
    document_id: ids.DocumentId,
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
        store.read, version.content_url, s3_client=s3_client
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


@router.get("/{document_id}/assets/{name}")
async def read_asset(
    document_id: ids.DocumentId,
    name: Annotated[str, fastapi.Path(pattern=ASSET_NAME)],
    s3_client: S3Client,
) -> fastapi.Response:
    """A picture a document's Markdown draws, named as the Markdown names it.

    The Markdown says `../assets/<name>`; this is where `<name>` is fetched from.
    Pictures belong to the document, so every version shares them.
    """
    media_type = mimetypes.guess_type(name)[0]
    if media_type not in ALLOWED_ASSET_MEDIA_TYPES:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=f"No asset {name}"
        )

    data = await run_in_threadpool(
        service.read_asset, document_id, name, s3_client=s3_client
    )
    if data is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=f"No asset {name}"
        )

    return fastapi.Response(
        content=data,
        media_type=media_type,
        # Named by the digest of its bytes, so what a name answers never changes.
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _reserved(
    staging: staging_store.StagingStore, source: schemas.Source
) -> tuple[staging_models.StagedDocument, ids.DocumentId, ids.VersionId]:
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


async def _sent(
    submitter: conversion.ConversionSubmitter,
    staging: staging_store.StagingStore,
    job: conversion.ConversionJob,
) -> None:
    """Send the job, or record that it could not be sent and answer 503."""
    try:
        await submitter.submit(job)
    except Exception as unsent:
        logger.exception("Could not send the conversion of file %s", job.file_id)
        await staging.fail_saving(job.file_id, "The conversion could not be started")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The conversion could not be started; try again",
        ) from unsent
