"""Deleting and rebuilding the search index, over HTTP, for the hub's admins.

Two operations, each its own route. A full rebuild from nothing is the delete, then the
rebuild. Nothing orders them or keeps one from overlapping the other: a delete while a
rebuild is running leaves the index partial, and the rebuild asked for again fills it.

The caller is the UI, which enforces `Hub.Admin` and vouches for the user; the API
only knows the caller is the UI, so `x-requested-by` is for the log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

import fastapi

from app import config
from app.admin import rebuild, schemas, tasks
from app.common import mongo
from app.search import search_service

if TYPE_CHECKING:
    import pymongo.asynchronous.database

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/admin/search-index", tags=["admin"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]


def get_search_admin() -> search_service.SearchServiceAdmin:
    return search_service.SearchServiceAdmin(config.get_config().search_api_base_url)


def get_search_index() -> search_service.SearchServiceIndex:
    return search_service.SearchServiceIndex(config.get_config().search_api_base_url)


SearchAdmin = Annotated[
    search_service.SearchServiceAdmin, fastapi.Depends(get_search_admin)
]
SearchIndex = Annotated[
    search_service.SearchServiceIndex, fastapi.Depends(get_search_index)
]
RequestedBy = Annotated[
    str,
    fastapi.Header(
        alias="x-requested-by",
        min_length=1,
        max_length=200,
        description="The hub user the UI vouches for; for the log only",
    ),
]


@router.delete("")
async def delete_search_index(
    requested_by: RequestedBy, search: SearchAdmin
) -> schemas.IndexDeleted:
    """Delete the search service's index and stores, so that search returns nothing
    until the rebuild gives it the guides again. Answers when it is done; a 502 says
    it failed, and asking again does no harm."""
    try:
        result = await search.reset()
    except search_service.SearchServiceError as failed:
        logger.warning("Search index delete by %s failed: %s", requested_by, failed)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="The search service could not delete its index",
        ) from failed
    logger.warning(
        "Search index delete by %s: %d objects and %d state records deleted",
        requested_by,
        result.deleted_objects,
        result.deleted_state_records,
    )
    return schemas.IndexDeleted.from_model(result)


@router.post("/rebuild", status_code=fastapi.status.HTTP_201_CREATED)
async def rebuild_search_index(
    request: schemas.RebuildRequest, database: Database, search: SearchIndex
) -> schemas.RebuildStarted:
    """Give the search service every guide's latest version again, as a save does. It
    deletes nothing first, and answers at once: the log says how it went."""
    tasks.start(
        rebuild.rebuild,
        database,
        search,
        request.requested_by,
        config.get_config().search_rebuild_concurrency,
    )
    return schemas.RebuildStarted()
