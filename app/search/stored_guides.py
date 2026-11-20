"""How the hub's own searches read the guides: a guide's latest version as stored, and
every guide's title."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import concurrency

from app.guidance import records
from app.guidance.documents import store
from app.search import in_hub, title_search

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pymongo.asynchronous.database

    from app.guidance import ids


class ContentMissingError(Exception):
    """The guide is recorded, but its content is not where the record says."""


def reader(
    database: pymongo.asynchronous.database.AsyncDatabase, s3_client: Any
) -> Callable[[ids.DocumentId], Awaitable[in_hub.Guide | None]]:
    """Reads a guide from the hub's records and store; None for no guide, or one whose
    document is not recorded yet."""

    async def read(guide_id: ids.DocumentId) -> in_hub.Guide | None:
        # The document is recorded last, so a guide without one is not there yet.
        document = await records.find(database, guide_id)
        version = await records.latest_version(database, guide_id)
        if document is None or version is None:
            return None

        content = await concurrency.run_in_threadpool(
            store.read, version.content_url, s3_client=s3_client
        )
        if content is None:
            message = f"Guide {guide_id} is recorded but its content is missing"
            raise ContentMissingError(message)

        return in_hub.Guide(version.id, content.decode("utf-8"))

    return read


def titles_reader(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> Callable[[], Awaitable[list[title_search.GuideTitle]]]:
    """Reads every guide's title, its latest version's, from the hub's records. A guide
    whose document is not recorded yet, or whose latest version has no title, has
    none."""

    async def read() -> list[title_search.GuideTitle]:
        titles = await records.latest_titles(database)
        # The document is recorded last, so a guide without one is not there yet.
        recorded = await records.existing(database, list(titles))
        return [
            title_search.GuideTitle(guide_id, title)
            for guide_id, title in titles.items()
            if guide_id in recorded
        ]

    return read
