"""Give the search service every guide again.

Each document's newest version is sent as a save sends it - the same details, the same
status - and nothing is deleted first: that is the reset, asked for separately. It is
safe on a live index. The search service applies a version it already holds as it did
before, and does not let an older version replace a newer one.

A version it refuses, or cannot be given after some tries, is logged and passed over,
so that one guide does not stop the rest. Nothing is raised out of the rebuild; the
summary it logs at the end is the only record of how it went.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.common import tracing
from app.guidance import models, records
from app.search import search_service

if TYPE_CHECKING:
    import pymongo.asynchronous.database

    from app.guidance import conversion

logger = logging.getLogger(__name__)

# Tries after the first, for a version the search service could not be given.
_RETRIES = 2
_RETRY_AFTER_SECONDS = 0.5


async def rebuild(
    database: pymongo.asynchronous.database.AsyncDatabase,
    search: conversion.SearchIndex,
    requested_by: str,
    concurrency: int = 4,
    *,
    retry_after: float = _RETRY_AFTER_SECONDS,
) -> None:
    """Send each document's newest version to `search`, `concurrency` at a time, and
    log how many were sent and how many failed. A document with no version is not
    sent."""
    try:
        guides = await records.every_latest_version(database)
    except Exception:
        logger.exception(
            "Search index rebuild by %s could not read guides", requested_by
        )
        return
    permits = asyncio.Semaphore(concurrency)
    sent = await asyncio.gather(
        *(
            _send(search, document, version, permits, retry_after)
            for document, version in guides
        )
    )
    logger.warning(
        "Search index rebuild by %s: %d guides sent, %d failed (trace id %s)",
        requested_by,
        sum(sent),
        len(sent) - sum(sent),
        tracing.ctx_trace_id.get(None),
    )


async def _send(
    search: conversion.SearchIndex,
    document: models.Document,
    version: models.Version,
    permits: asyncio.Semaphore,
    retry_after: float,
) -> bool:
    """Whether the version was given to the search service."""
    async with permits:
        for attempt in range(_RETRIES + 1):
            try:
                await search.index(version, models.search_details(document, version))
            except search_service.IndexRefusedError as refused:
                logger.exception(
                    "Search refused document %s version %s: %s",
                    document.id,
                    version.id,
                    refused,
                )
                return False
            except search_service.SearchServiceError as failed:
                if attempt < _RETRIES:
                    await asyncio.sleep(retry_after * (attempt + 1))
                    continue
                logger.exception(
                    "Search could not be given document %s version %s: %s",
                    document.id,
                    version.id,
                    failed,
                )
                return False
            except Exception:
                logger.exception(
                    "Document %s version %s could not be sent", document.id, version.id
                )
                return False
            return True
    return False
