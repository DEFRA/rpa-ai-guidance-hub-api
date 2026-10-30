import copy
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger
from typing import Any

import fastapi
import fastapi.security
import uvicorn

from app import config as app_config
from app.auth import service as auth_service
from app.common import mongo, tracing
from app.guidance import records
from app.guidance import router as guidance_router
from app.guidance.documents.staging import router as staging_router
from app.health import router as health_router
from app.reference import router as reference_router
from app.review import router as review_router

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(_: fastapi.FastAPI) -> AsyncGenerator[None]:
    if app_config.get_config().use_auth:
        logger.info("Using WebIdentity authentication for API routes")

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

_known_caller = auth_service.allow_all_known()

# Puts the bearer scheme (and a `security` entry per operation) into the generated
# OpenAPI document for the routes it guards. It enforces nothing: `auto_error=False`
# makes it return None when the header is absent, and `_require_auth_if_enabled`
# does the checking and logging itself.
_bearer_scheme = fastapi.security.HTTPBearer(auto_error=False)


async def _require_auth_if_enabled(request: fastapi.Request) -> None:
    """
    Guard a route when `USE_AUTH` is on.

    Decided per request rather than when this module is imported, so importing the
    app (the OpenAPI export, tooling) never needs a valid configuration.
    """
    if app_config.get_config().use_auth:
        await _known_caller(request)


protected_routes = fastapi.APIRouter(
    dependencies=[
        fastapi.Depends(_bearer_scheme),
        fastapi.Depends(_require_auth_if_enabled),
    ]
)

protected_routes.include_router(reference_router.router)
protected_routes.include_router(review_router.router)
protected_routes.include_router(guidance_router.router)
protected_routes.include_router(staging_router.router)

app.include_router(health_router.router)
app.include_router(staging_router.callback_router)
app.include_router(protected_routes)

_generated_openapi = app.openapi


def openapi_schema(*, auth: bool) -> dict[str, Any]:
    """
    The OpenAPI document, describing authentication only if `auth` is true.

    The bearer scheme is declared on the guarded routes whenever the schema is
    generated, but whether those routes enforce it depends on `USE_AUTH`. A
    document claiming a token is needed would mislead anyone running with auth
    off, so it is stripped out in that case. The committed `docs/openapi.json`
    is built with `auth=True`, matching a deployed service.
    """
    documented = _generated_openapi()
    if auth:
        return documented

    schema = copy.deepcopy(documented)
    schema.get("components", {}).pop("securitySchemes", None)
    for operations in schema["paths"].values():
        for operation in operations.values():
            if isinstance(operation, dict):
                operation.pop("security", None)
    return schema


# Read per call rather than at import, so importing the app never needs config.
app.openapi = lambda: openapi_schema(  # type: ignore[method-assign]
    auth=app_config.get_config().use_auth
)


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
