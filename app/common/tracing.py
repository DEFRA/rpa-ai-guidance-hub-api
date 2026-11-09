import contextvars
import logging
from typing import Any

import fastapi
from starlette.middleware import base as middleware_base

from app import config as app_config

logger = logging.getLogger(__name__)

ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id")
ctx_request: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "request", default=None
)
ctx_response: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "response", default=None
)


class TraceIdMiddleware(middleware_base.BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: fastapi.Request,
        call_next: middleware_base.RequestResponseEndpoint,
    ) -> fastapi.Response:
        req_trace_id = request.headers.get(app_config.get_config().tracing_header, None)
        if req_trace_id:
            ctx_trace_id.set(req_trace_id)

        ctx_request.set({"url": str(request.url), "method": request.method})

        response: fastapi.Response = await call_next(request)
        ctx_response.set({"status_code": response.status_code})
        return response
