"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import records as guidance_records
from app.guidance import section_ids
from app.guidance import service as guidance_service
from app.users import models

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterable, Mapping, Sequence

    import pymongo.asynchronous.database


async def pinned(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user: models.User,
    filters: Mapping[str, Sequence[str | None]],
) -> tuple[
    list[tuple[guidance_models.DocumentSummary, int]],
    dict[str, list[guidance_models.OptionCount]],
]:
    """The whole guides the user has pinned, in their order, that the filters let
    through, as `GET /guides` lists them, each with its position among all the
    user's pins, sections too; and each filter's values among all the whole guides
    pinned, with their counts. A guide that no longer exists is left out, of the
    list and of the positions, as a patch of the pins leaves it out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    named = [pin.document_id for pin in user.pins]
    existing = await guidance_records.existing(database, named)
    live = [pin for pin in user.pins if pin.document_id in existing]
    whole = {
        pin.document_id: position
        for position, pin in enumerate(live)
        if pin.section_id is None
    }
    summaries, counts = await _listed(database, list(whole), filters)
    listed = [
        (summaries[guide], position)
        for guide, position in whole.items()
        if guide in summaries
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


async def guides_in_brief(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user: models.User,
    s3_client: Any,
) -> dict[guidance_ids.DocumentId, models.GuideInBrief]:
    """The guides the user's pins and cases name, for the side navigation, each with
    its title (its newest version's, else what its author called it) and, for a
    guide with a section pinned or bookmarked, its sections. A guide that no longer
    exists is left out."""
    pinned = {pin.document_id for pin in user.pins}
    sectioned = {pin.document_id for pin in user.pins if pin.section_id is not None}
    sectioned |= {b.document_id for case in user.cases for b in case.bookmarks}
    documents = await guidance_records.find_many(database, list(pinned | sectioned))
    latest = await guidance_records.latest_versions_of(database, list(documents))
    given = await section_ids.of_latest(
        database,
        {guide: version for guide, version in latest.items() if guide in sectioned},
        s3_client,
    )
    return {
        guide: models.GuideInBrief(
            title=_title(document, latest.get(guide)),
            sections=models.GuideSections(given.get(guide, {})),
        )
        for guide, document in documents.items()
    }


async def guide_sections(
    database: pymongo.asynchronous.database.AsyncDatabase,
    guides: Iterable[guidance_ids.DocumentId],
    s3_client: Any,
) -> dict[guidance_ids.DocumentId, models.GuideSections]:
    """Each guide's sections as its latest version has them, given their ids if they
    have none yet. A guide with no version is left out, and so is a version with no
    guide: saving writes the guide last, so one that failed between leaves a version
    nothing lists."""
    existing = await guidance_records.existing(database, list(guides))
    latest = await guidance_records.latest_versions_of(database, list(existing))
    given = await section_ids.of_latest(database, latest, s3_client)
    return {
        guide: models.GuideSections(by_anchor) for guide, by_anchor in given.items()
    }


def _title(
    document: guidance_models.Document, newest: guidance_models.Version | None
) -> str | None:
    """A guide's title, for the side navigation: its newest version's, else what its
    author called it."""
    if newest is not None and newest.title:
        return newest.title
    return document.metadata.get("guideTitle")
