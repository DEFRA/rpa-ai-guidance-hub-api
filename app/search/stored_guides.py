"""How the hub's own search reads a guide: its latest version as stored, and the title
the guide's page shows, the version's own or else the one its author gave."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import concurrency

from app.guidance import records
from app.guidance.documents import store
from app.search import in_hub

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pymongo.asynchronous.database

    from app.guidance import ids


class ContentMissingError(Exception):
    """The guide is recorded, but its content is not where the record says."""


def reader(
    database: pymongo.asynchronous.database.AsyncDatabase, s3_client: Any
) -> Callable[[ids.DocumentId], Awaitable[in_hub.Guide | None]]:
    """Reads a guide from the hub's records and store; None for no guide."""

    async def read(guide_id: ids.DocumentId) -> in_hub.Guide | None:
        version = await records.latest_version(database, guide_id)
        if version is None:
            return None

        content = await concurrency.run_in_threadpool(
            store.read, version.content_url, s3_client=s3_client
        )
        if content is None:
            message = f"Guide {guide_id} is recorded but its content is missing"
            raise ContentMissingError(message)

        document = await records.find(database, guide_id)
        given = None if document is None else document.metadata.get("guideTitle")
        return in_hub.Guide(
            version.id, version.title or given or "", content.decode("utf-8")
        )

    return read
