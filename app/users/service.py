"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.guidance import ids as guidance_ids
from app.guidance import records as guidance_records

if TYPE_CHECKING:
    import pymongo.asynchronous.database

    from app.users import models


async def pinned_titles(
    database: pymongo.asynchronous.database.AsyncDatabase, user: models.User
) -> list[tuple[guidance_ids.DocumentId, str | None]]:
    """The guides the user has pinned, in their order, each with its title, for the
    side navigation: its newest version's, else what its author called it. A guide
    that no longer exists is left out."""
    documents = await guidance_records.find_many(database, list(user.pins))
    latest = await guidance_records.latest_versions_of(database, list(documents))

    def title(pin: guidance_ids.DocumentId) -> str | None:
        newest = latest.get(pin)
        if newest is not None and newest.title:
            return newest.title
        return documents[pin].metadata.get("guideTitle")

    return [(pin, title(pin)) for pin in user.pins if pin in documents]
