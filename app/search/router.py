"""Search in one guide, over HTTP. The guide's page asks here; this API chooses where
the search is done (`FEATURE_SEARCH_ONE_GUIDE`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import fastapi

from app import config
from app.common import mongo, s3
from app.guidance import ids
from app.search import guide_search, in_hub, schemas, search_service, stored_guides

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/guides", tags=["search"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]
S3Client = Annotated[Any, fastapi.Depends(s3.get_s3_client)]


def get_guide_search(
    database: Database, s3_client: S3Client
) -> guide_search.GuideSearch:
    """The search in one guide that the environment chooses."""
    settings = config.get_config()
    if settings.feature_search_one_guide is not config.SearchImplementation.SEARCH_API:
        return in_hub.InHubGuideSearch(stored_guides.reader(database, s3_client))
    if settings.search_api_base_url is None:
        message = "Search in one guide goes to the search API, but SEARCH_API_BASE_URL is not set"
        raise RuntimeError(message)
    return search_service.SearchServiceGuideSearch(settings.search_api_base_url)


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
    if found is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )
    return schemas.GuideMatches.from_model(found)
