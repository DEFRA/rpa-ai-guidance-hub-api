from logging import getLogger
from typing import Any

import fastapi
import pymongo

from app import config as app_config
from app.common import mongo, s3
from app.guidance.drafts import schemas, service, store

config = app_config.get_config()

router = fastapi.APIRouter(prefix="/guidance/drafts")
logger = getLogger(__name__)


async def get_draft_store(
    db: pymongo.asynchronous.database.AsyncDatabase = fastapi.Depends(mongo.get_db),
) -> store.DraftStore:
    draft_store = store.MongoDraftStore(db, config.draft_retention_seconds)

    await draft_store.ensure_indexes()

    return draft_store


async def get_draft_service(
    draft_store: store.DraftStore = fastapi.Depends(get_draft_store),
    s3_client: Any = fastapi.Depends(s3.get_s3_client),
) -> service.DraftService:
    return service.DraftService(draft_store, config.source_docs_s3_bucket, s3_client)


@router.post("/callback", status_code=fastapi.status.HTTP_202_ACCEPTED)
async def handle_callback(
    payload: schemas.UploadCallbackPayload,
    background_tasks: fastapi.BackgroundTasks,
    drafts: service.DraftService = fastapi.Depends(get_draft_service),
) -> fastapi.Response:
    documents = payload.uploaded_documents()

    if not documents:
        logger.info("Callback named no completed file")
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_204_NO_CONTENT)

    for document in documents:
        claimed = await drafts.handle_callback(document)

        if not claimed:
            logger.info(
                "Ignoring duplicate callback for file %s: already claimed",
                document.file_id,
            )
            continue

        background_tasks.add_task(drafts.minimal_parse, document)

    return fastapi.Response(status_code=fastapi.status.HTTP_202_ACCEPTED)


@router.get("/{file_id}")
async def get_draft(
    file_id: str,
    drafts: service.DraftService = fastapi.Depends(get_draft_service),
) -> schemas.DraftStatusResponse:
    draft = await drafts.get_draft(file_id)

    if not draft:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND)

    return schemas.DraftStatusResponse.from_guide_draft(draft)
