"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

import asyncio
import itertools
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
            heading=_heading(pin, sections),
        )
        for position, pin in shown
    ]
    return listed, counts


def _heading(
    pin: models.Pin,
    sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
) -> str | None:
    """A pinned section's heading as its guide's latest version has it, else as the
    pin kept it; none for a whole guide."""
    if pin.section_id is None or pin.document_id not in sections:
        return pin.heading
    return sections[pin.document_id].heading_of(pin.section_id, pin.heading)


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
    named = _sections_named(user)
    sectioned = set(named)

    documents = await guidance_records.find_many(database, list(pinned | sectioned))
    latest = await guidance_records.latest_versions_of(database, list(documents))

    with_sections = {
        guide: version for guide, version in latest.items() if guide in sectioned
    }
    given = await section_ids.of_latest(database, with_sections, s3_client)
    headings = await section_ids.headings_of(
        database, [version.id for version in with_sections.values()]
    )
    # Only a section gone from the latest version needs the versions before it.
    gone = [
        guide
        for guide in with_sections
        if named[guide] - set(given.get(guide, {}).values())
    ]
    versions = await guidance_records.versions_of_each(database, gone) if gone else {}

    return {
        guide: models.GuideInBrief(
            title=_title(document, latest.get(guide)),
            sections=models.GuideSections(
                given.get(guide, {}),
                _headings_of(guide, with_sections, headings),
                _removed(versions.get(guide, []), given.get(guide, {})),
            ),
        )
        for guide, document in documents.items()
    }


def _sections_named(
    user: models.User,
) -> dict[guidance_ids.DocumentId, set[guidance_ids.SectionId]]:
    """The sections the user's pins and case bookmarks name, by guide."""
    named: dict[guidance_ids.DocumentId, set[guidance_ids.SectionId]] = {}
    for pin in user.pins:
        if pin.section_id is not None:
            named.setdefault(pin.document_id, set()).add(pin.section_id)
    for case in user.cases:
        for bookmark in case.bookmarks:
            named.setdefault(bookmark.document_id, set()).add(bookmark.section_id)
    return named


def _removed(
    versions: Sequence[guidance_models.Version],
    latest: Mapping[str, guidance_ids.SectionId],
) -> dict[guidance_ids.SectionId, models.RemovedSection]:
    """Each section an earlier version of a guide had that its latest version does
    not, from its versions newest first: the version after the last one that had it
    removed it."""
    now = set(latest.values())
    removed: dict[guidance_ids.SectionId, models.RemovedSection] = {}
    for newer, older in itertools.pairwise(versions):
        for section in (older.sections or {}).values():
            if section.id not in now and section.id not in removed:
                removed[section.id] = models.RemovedSection(
                    newer.version_number, section.heading
                )
    return removed


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
    headings = await section_ids.headings_of(
        database, [version.id for version in latest.values()]
    )

    return {
        guide: models.GuideSections(by_anchor, _headings_of(guide, latest, headings))
        for guide, by_anchor in given.items()
    }


def _headings_of(
    guide: guidance_ids.DocumentId,
    latest: Mapping[guidance_ids.DocumentId, guidance_models.Version],
    headings: Mapping[guidance_ids.VersionId, Mapping[guidance_ids.SectionId, str]],
) -> Mapping[guidance_ids.SectionId, str]:
    """The headings `guide`'s latest version keeps, by section id; none if it keeps
    none."""
    version = latest.get(guide)
    return {} if version is None else headings.get(version.id, {})


def _title(
    document: guidance_models.Document, newest: guidance_models.Version | None
) -> str | None:
    """A guide's title, for the side navigation: its newest version's, else what its
    author called it."""
    if newest is not None and newest.title:
        return newest.title
    return (document.metadata or {}).get("guideTitle")
