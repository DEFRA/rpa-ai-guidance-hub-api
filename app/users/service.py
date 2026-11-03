"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import records as guidance_records
from app.guidance import service as guidance_service

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pymongo.asynchronous.database

    from app.users import models


async def pinned(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user: models.User,
    filters: Mapping[str, Sequence[str | None]],
) -> tuple[
    list[tuple[guidance_models.DocumentSummary, int]],
    dict[str, list[guidance_models.OptionCount]],
]:
    """The guides the user has pinned, in their order, that the filters let
    through, as `GET /guides` lists them, each with its position among all the
    user's pins; and each filter's values among all the pinned guides, with their
    counts. A guide that no longer exists is left out, of the list and of the
    positions, as a patch of the pins leaves it out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    metadata = guidance_service.metadata_filters(filters)
    existing = await guidance_records.existing(database, list(user.pins))
    live = [pin for pin in user.pins if pin in existing]
    shown = await guidance_records.matching(database, live, metadata)
    documents = await guidance_records.find_many(database, list(shown))
    latest = await guidance_records.latest_versions_of(database, list(documents))
    listed = [
        (guidance_models.DocumentSummary(documents[pin], latest.get(pin)), position)
        for position, pin in enumerate(live)
        if pin in documents
    ]
    counts = await guidance_service.option_counts(
        database, metadata, only=list(user.pins)
    )
    return listed, counts


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
