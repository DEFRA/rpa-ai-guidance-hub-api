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
    s3_client: Any,
) -> tuple[list[models.ListedPin], dict[str, list[guidance_models.OptionCount]]]:
    """Every pin of the user's, guides and sections of guides, in their order, whose
    guide the filters let through, each with its position among all the user's pins;
    and each filter's values among the guides pinned, whole or by a section, each
    guide counted once. A guide that no longer exists is left out, of the list and
    of the positions, as a patch of the pins leaves it out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    named = [pin.document_id for pin in user.pins]
    existing = await guidance_records.existing(database, named)
    live = [pin for pin in user.pins if pin.document_id in existing]
    guides = list(dict.fromkeys(pin.document_id for pin in live))
    summaries, counts = await _listed(database, guides, filters)
    shown = [
        (position, pin)
        for position, pin in enumerate(live)
        if pin.document_id in summaries
    ]
    sections = await guide_sections(
        database,
        {pin.document_id for _, pin in shown if pin.section_id is not None},
        s3_client,
    )
    listed = [
        models.ListedPin(
            summary=summaries[pin.document_id],
            pin=pin,
            anchor=_anchor(pin, sections),
            position=position,
        )
        for position, pin in shown
    ]
    return listed, counts


def _anchor(
    pin: models.Pin,
    sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
) -> str | None:
    """A pinned section's anchor in its guide's latest version; none for a whole
    guide, or for a section the guide no longer has."""
    if pin.section_id is None or pin.document_id not in sections:
        return None
    return sections[pin.document_id].anchor_of(pin.section_id)


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


async def bookmarked(
    database: pymongo.asynchronous.database.AsyncDatabase,
    cases: Sequence[models.Case],
    filters: Mapping[str, Sequence[str | None]],
) -> tuple[
    list[guidance_models.DocumentSummary],
    dict[str, list[guidance_models.OptionCount]],
]:
    """The guides bookmarked to the cases, each once, in the order of the cases and,
    in each, the order the user put them, that the filters let through, as
    `GET /guides` lists them; and each filter's values among all those guides, with
    their counts. A guide that no longer exists is left out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    guides = list(
        dict.fromkeys(
            bookmark.document_id for case in cases for bookmark in case.bookmarks
        )
    )
    summaries, counts = await _listed(database, guides, filters)
    return [summaries[guide] for guide in guides if guide in summaries], counts


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
