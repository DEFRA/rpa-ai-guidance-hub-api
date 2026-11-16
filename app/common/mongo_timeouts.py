"""Client-side MongoDB operation timeouts, and translation of timeout failures into a
domain exception.

Most operations run inside a repository method, so the timeout is applied per method,
sized to what the method does (see the tier constants), rather than as one app-wide
timeout. Each method is wrapped in ``pymongo.timeout``: the deadline set on entering
the method covers every operation it awaits, and a nested ``pymongo.timeout`` can only
shorten it, never extend it.

A method that spans several operations (for example ``list_documents``) is decorated on
its own; the helpers it calls are left unwrapped so they inherit the outer deadline.
Do not decorate a method that also waits on something other than Mongo (S3, say): that
time would count against the deadline. Decorate the Mongo part of it instead.

Deliberately not timed: index creation (``ensure_indexes``) and startup migrations.
They must finish however long they take, and they run before the service takes
requests.
"""

from __future__ import annotations

import http
from functools import wraps
from logging import getLogger
from os import environ
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar, cast

import fastapi
import fastapi.responses
import pymongo
import pymongo.errors

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = getLogger(__name__)

# The exception type reported for structured (ECS) logging, so logs can be keyed on it.
EXCEPTION_TYPE = "MongoTimeout"


def _env_seconds(name: str, default: float) -> float:
    """The timeout for a tier, or ``default`` when ``MONGO_{name}_SECONDS`` is unset."""
    raw = environ.get(f"MONGO_{name}_SECONDS")
    return default if raw is None else float(raw)


# A single document / indexed lookup: ``find_one``, ``insert_one``, ``update_one`` by
# ``_id``, and upserts. FAST <= STANDARD <= HEAVY.
FAST: float = _env_seconds("FAST", 2.0)
# A filtered find with a limit, or ``count_documents``.
STANDARD: float = _env_seconds("STANDARD", 5.0)
# Aggregations, ``update_many``/``delete_many``, ``bulk_write``, exports.
HEAVY: float = _env_seconds("HEAVY", 30.0)

F = TypeVar("F", bound="Callable[..., Awaitable[Any]]")


class MongoTimeoutError(RuntimeError):
    """Raised when a MongoDB operation exceeds its client-side timeout.

    ``operation`` and ``timeout`` are the method and the seconds of its tier. When the
    failure is logged, they appear as ECS extras (``operation``, ``timeout_seconds``)
    alongside ``exception_type``.
    """

    exception_type = EXCEPTION_TYPE

    def __init__(self, message: str, *, operation: str, timeout: float) -> None:
        super().__init__(message)
        self.operation = operation
        self.timeout = timeout


def _raise_if_timeout(
    error: pymongo.errors.PyMongoError, operation: str, timeout: float
) -> NoReturn:
    """Log and raise `MongoTimeoutError` for a timeout; re-raise anything else as it
    was, so existing handling (for example of `DuplicateKeyError`) is preserved."""
    if not error.timeout:
        raise error

    logger.error(
        "MongoDB operation %s timed out after %gs",
        operation,
        timeout,
        extra={
            "exception_type": EXCEPTION_TYPE,
            "operation": operation,
            "timeout_seconds": timeout,
        },
        exc_info=error,
    )
    message = f"MongoDB operation {operation!r} timed out after {timeout:g}s"
    raise MongoTimeoutError(message, operation=operation, timeout=timeout) from error


def mongo(timeout: float) -> Callable[[F], F]:
    """Wrap an async repository method in a ``pymongo.timeout`` of ``timeout`` seconds."""

    def decorate(func: F) -> F:
        operation = func.__qualname__

        @wraps(func)
        async def timed(*args: Any, **kwargs: Any) -> Any:
            with pymongo.timeout(timeout):
                try:
                    return await func(*args, **kwargs)
                except pymongo.errors.PyMongoError as error:
                    _raise_if_timeout(error, operation, timeout)

        return cast("F", timed)

    return decorate


async def handle(_request: fastapi.Request, _error: Exception) -> fastapi.Response:
    """Answer a request whose MongoDB operation timed out: try again later."""
    return fastapi.responses.JSONResponse(
        status_code=http.HTTPStatus.SERVICE_UNAVAILABLE,
        content={"detail": "The service is busy. Try again shortly."},
        headers={"Retry-After": "5"},
    )
