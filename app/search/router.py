"""Search in one guide, and finding guides by title, over HTTP. This API chooses where
each is done: `FEATURE_SEARCH_ONE_GUIDE` and `FEATURE_SEARCH_ALL_TITLES`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

import fastapi

from app import config
from app.common import mongo, s3
from app.guidance import ids
from app.search import (
    guide_search,
    in_hub,
    schemas,
    search_service,
    stored_guides,
    title_search,
)

if TYPE_CHECKING:
    import pymongo.asynchronous.database

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/guides", tags=["search"])
# Not under /guides, where GET /guides/{document_id} would take "titles" for an id.
titles_router: fastapi.APIRouter = fastapi.APIRouter(prefix="/search", tags=["search"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]
S3Client = Annotated[Any, fastapi.Depends(s3.get_s3_client)]

_SEARCH_API: config.SearchImplementation = config.SearchImplementation.SEARCH_API


def get_guide_search(
    database: Database, s3_client: S3Client
) -> guide_search.GuideSearch:
    """The search in one guide that the environment chooses."""
    if config.get_config().feature_search_one_guide is not _SEARCH_API:
        return in_hub.InHubGuideSearch(stored_guides.reader(database, s3_client))
    return search_service.SearchServiceGuideSearch(
        _search_api_address("Search in one guide")
    )


def _search_api_address(search: str) -> str:
    address = config.get_config().search_api_base_url
    if address is None:
        message = f"{search} goes to the search API, but SEARCH_API_BASE_URL is not set"
        raise RuntimeError(message)
    return address


GuideSearch = Annotated[guide_search.GuideSearch, fastapi.Depends(get_guide_search)]


@router.get("/{document_id}/search")
async def search_one_guide(
    document_id: ids.DocumentId,
    q: Annotated[str, fastapi.Query(max_length=500, description="The words to find")],
    search: GuideSearch,
) -> schemas.GuideMatches:
    """Where the words are in the guide's latest version: in each section that holds
    them."""
    try:
        found = await search.in_guide(document_id, q)
    except stored_guides.ContentMissingError as missing:
        # Not a 404, which would say the guide does not exist when it does.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY, detail=str(missing)
        ) from missing
    except search_service.SearchServiceError as failed:
        raise _search_service_unavailable(failed) from failed
    if found is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )
    return schemas.GuideMatches.from_model(found)


def get_title_search(database: Database) -> title_search.TitleSearch:
    """Finding guides by title, where the environment chooses."""
    if config.get_config().feature_search_all_titles is not _SEARCH_API:
        return in_hub.InHubTitleSearch(stored_guides.titles_reader(database))
    return search_service.SearchServiceTitleSearch(
        _search_api_address("Finding guides by title")
    )


TitleSearch = Annotated[title_search.TitleSearch, fastapi.Depends(get_title_search)]


@titles_router.get("/titles")
async def find_guides_by_title(
    q: Annotated[
        str,
        fastapi.Query(
            min_length=1, max_length=500, description="A title, or words like it"
        ),
    ],
    search: TitleSearch,
    limit: Annotated[
        int, fastapi.Query(ge=1, le=50, description="How many guides to answer at most")
    ] = 10,
) -> schemas.GuideTitles:
    """The guides with titles like the words, the likest first: with a typo, half a
    word, the words in another order, or only some of them."""
    try:
        found = await search.like(q, limit)
    except search_service.SearchServiceError as failed:
        raise _search_service_unavailable(failed) from failed
    return schemas.GuideTitles.from_model(found)


def _search_service_unavailable(
    failed: search_service.SearchServiceError,
) -> fastapi.HTTPException:
    """A 502: the search service failed, not this API. Its address stays in the log."""
    logger.warning("%s", failed)
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
        detail="The search service is not available",
    )
