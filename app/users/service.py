"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import records as guidance_records
from app.guidance import service as guidance_service

if TYPE_CHECKING:
    import datetime as dt
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
    existing = await guidance_records.existing(database, list(user.pins))
    live = [pin for pin in user.pins if pin in existing]
    summaries, counts = await _listed(database, live, filters)
    listed = [
        (summaries[pin], position)
        for position, pin in enumerate(live)
        if pin in summaries
    ]
    return listed, counts


async def recently_opened(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user: models.User,
    filters: Mapping[str, Sequence[str | None]],
) -> tuple[
    list[tuple[guidance_models.DocumentSummary, dt.datetime]],
    dict[str, list[guidance_models.OptionCount]],
]:
    """The guides the user opened most recently, newest first, that the filters let
    through, as `GET /guides` lists them, each with when the user last opened it;
    and each filter's values among all those guides, with their counts. A guide
    that no longer exists is left out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    opened = user.recently_opened
    summaries, counts = await _listed(
        database, [guide.document_id for guide in opened], filters
    )
    listed = [
        (summaries[guide.document_id], guide.opened_at)
        for guide in opened
        if guide.document_id in summaries
    ]
    return listed, counts


async def _listed(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[guidance_ids.DocumentId],
    filters: Mapping[str, Sequence[str | None]],
) -> tuple[
    dict[guidance_ids.DocumentId, guidance_models.DocumentSummary],
    dict[str, list[guidance_models.OptionCount]],
]:
    """Of the guides in one of the user's lists, those the filters let through, as
    summaries by id; and each filter's values among all the list's guides, with
    their counts. A guide that no longer exists is in neither.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    metadata = guidance_service.metadata_filters(filters)
    summaries, counts = await asyncio.gather(
        _summaries(database, document_ids, metadata),
        guidance_service.option_counts(database, metadata, only=list(document_ids)),
    )
    return summaries, counts


async def _summaries(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[guidance_ids.DocumentId],
    metadata: Mapping[str, Sequence[str | None]],
) -> dict[guidance_ids.DocumentId, guidance_models.DocumentSummary]:
    """Those of the guides whose metadata match, as summaries by id. A guide that
    no longer exists is left out."""
    shown = await guidance_records.matching(database, document_ids, metadata)
    documents = await guidance_records.find_many(database, list(shown))
    latest = await guidance_records.latest_versions_of(database, list(documents))
    return {
        guide: guidance_models.DocumentSummary(document, latest.get(guide))
        for guide, document in documents.items()
    }


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
