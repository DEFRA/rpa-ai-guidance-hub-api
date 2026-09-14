from logging import getLogger
from typing import Annotated, Any

import fastapi
import pymongo

from app import config as app_config
from app.common import mongo, s3
from app.guidance.documents.staging import schemas, service, store

config = app_config.get_config()

router = fastapi.APIRouter(prefix="/guide/staging")
logger = getLogger(__name__)


def get_staging_store(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase, fastapi.Depends(mongo.get_db)
    ],
) -> store.StagingStore:
    return store.MongoStagingStore(db, config.staging_retention_seconds)


def get_staging_service(
    staging_store: Annotated[store.StagingStore, fastapi.Depends(get_staging_store)],
    s3_client: Annotated[Any, fastapi.Depends(s3.get_s3_client)],
) -> service.StagingService:
    return service.StagingService(
        staging_store, config.source_docs_s3_bucket, s3_client
    )


@router.post("/callback", status_code=fastapi.status.HTTP_202_ACCEPTED)
async def handle_callback(
    payload: schemas.UploadCallbackPayload,
    background_tasks: fastapi.BackgroundTasks,
    staging: Annotated[service.StagingService, fastapi.Depends(get_staging_service)],
) -> fastapi.Response:
    documents = payload.uploaded_documents()

    if not documents:
        logger.info("Callback named no completed file")
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_204_NO_CONTENT)

    for document in documents:
        claimed = await staging.handle_callback(document)

        if not claimed:
            logger.info(
                "Ignoring duplicate callback for file %s: already claimed",
                document.file_id,
            )
            continue

        background_tasks.add_task(staging.minimal_parse, document)

    return fastapi.Response(status_code=fastapi.status.HTTP_202_ACCEPTED)


@router.get("/{file_id}")
async def get_staged_document(
    file_id: str,
    staging: Annotated[service.StagingService, fastapi.Depends(get_staging_service)],
) -> schemas.StagedDocumentResponse:
    staged_document = await staging.get_staged_doc(file_id)

    if not staged_document:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND)

    return schemas.StagedDocumentResponse.from_staged_document(staged_document)
