import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger

import fastapi
import uvicorn

from app import config as app_config
from app.common import mongo, tracing
from app.guidance import records
from app.guidance import router as guidance_router
from app.guidance.documents.staging import router as staging_router
from app.health import router as health_router
from app.reference import router as reference_router
from app.review import router as review_router
from app.auth import service as auth_service

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI) -> AsyncGenerator[None]:
    client = await mongo.get_mongo_client()
    logger.info("MongoDB client connected")

    # Made at startup rather than on the first write, so that whatever races the
    # first two requests is racing an index that already exists.
    await records.ensure_indexes(await mongo.get_db(client))
    yield
    if client:
        await client.close()
        logger.info("MongoDB client closed")


app = fastapi.FastAPI(lifespan=lifespan, title="RPA AI Guidance Hub API")

app.add_middleware(tracing.TraceIdMiddleware)

auth_deps = []

if app_config.get_config().use_auth:
    logger.info("Using WebIdentity authentication for API routes")
    auth_deps.append(fastapi.Depends(auth_service.verify_caller))

protected_routes = fastapi.APIRouter(dependencies=auth_deps)

protected_routes.include_router(reference_router.router)
protected_routes.include_router(review_router.router)
protected_routes.include_router(guidance_router.router)
protected_routes.include_router(staging_router.router)

app.include_router(health_router.router)
app.include_router(protected_routes)

def main() -> None:  # pragma: no cover
    config = app_config.get_config()

    if config.http_proxy:
        os.environ["HTTP_PROXY"] = str(config.http_proxy)
        os.environ["HTTPS_PROXY"] = str(config.http_proxy)
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)

    uvicorn.run(
        "app.entrypoints.fastapi:app",
        host=config.host,
        port=config.port,
        log_config=config.log_config,
        reload=config.python_env == "development",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
