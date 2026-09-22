from logging import getLogger

import httpx2

from app import config as app_config
from app.common import tracing

logger = getLogger(__name__)


async def async_hook_request_tracing(request: httpx2.Request) -> None:
    trace_id = tracing.ctx_trace_id.get(None)
    if trace_id:
        request.headers[app_config.get_config().tracing_header] = trace_id


def hook_request_tracing(request: httpx2.Request) -> None:
    trace_id = tracing.ctx_trace_id.get(None)
    if trace_id:
        request.headers[app_config.get_config().tracing_header] = trace_id


def create_async_client(request_timeout: int = 30) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        timeout=request_timeout,
        event_hooks={"request": [async_hook_request_tracing]},
    )


def create_client(request_timeout: int = 30) -> httpx2.Client:
    return httpx2.Client(
        timeout=request_timeout,
        event_hooks={"request": [hook_request_tracing]},
    )
