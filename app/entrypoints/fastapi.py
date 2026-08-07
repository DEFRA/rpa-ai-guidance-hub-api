import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger

import fastapi
import uvicorn

from app import config as app_config
from app.common import mongo, tracing
from app.health import router as health_router
from app.review import router as review_router

logger = getLogger(__name__)

config = app_config.get_config()


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI) -> AsyncGenerator[None]:
    client = await mongo.get_mongo_client()
    logger.info("MongoDB client connected")
    yield
    if client:
        await client.close()
        logger.info("MongoDB client closed")


app = fastapi.FastAPI(lifespan=lifespan, title="RPA AI Guidance Hub API")

app.add_middleware(tracing.TraceIdMiddleware)

app.include_router(health_router.router)
app.include_router(review_router.router)


def main() -> None:  # pragma: no cover
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
