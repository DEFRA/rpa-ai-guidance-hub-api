"""What the hub shows of a user's own lists, read with the guides they name."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import records as guidance_records
from app.guidance import section_ids
from app.guidance import service as guidance_service
from app.guidance.documents import paragraphs, reader, store
from app.guidance.parsing import anchors
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
    s3_client: Any,
    *,
    keep_empty: bool = False,
) -> tuple[list[models.BookmarkedCase], dict[str, list[guidance_models.OptionCount]]]:
    """The cases, in their order, each with the guides bookmarked to it that the
    filters let through, in the user's order, each with its bookmarked sections in
    the guide's order; and each filter's values among all the cases' guides, each
    guide counted once. A case the filters leave empty is left out, unless
    `keep_empty`; a guide that no longer exists is left out.

    Raises:
        guidance_service.UnknownFilterValueError: if a filter value is not in the
            reference data.
    """
    guides = list(
        dict.fromkeys(bookmark.document_id for c in cases for bookmark in c.bookmarks)
    )
    summaries, counts = await _listed(database, guides, filters)
    sections, first_paragraphs = await asyncio.gather(
        guide_sections(database, summaries, s3_client),
        _first_paragraphs(database, summaries, s3_client),
    )
    listed = [
        models.BookmarkedCase(
            case=case,
            guides=tuple(
                _bookmarked_guides(case, summaries, sections, first_paragraphs)
            ),
        )
        for case in cases
    ]
    return [case for case in listed if case.guides or keep_empty], counts


def _bookmarked_guides(
    case: models.Case,
    summaries: Mapping[guidance_ids.DocumentId, guidance_models.DocumentSummary],
    sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
    first_paragraphs: Mapping[guidance_ids.DocumentId, Mapping[str, str | None]],
) -> list[models.BookmarkedGuide]:
    """The case's guides that `summaries` has, in the order first bookmarked, each
    with its bookmarked sections in the guide's order."""
    by_guide: dict[guidance_ids.DocumentId, list[models.Bookmark]] = {}
    for bookmark in case.bookmarks:
        by_guide.setdefault(bookmark.document_id, []).append(bookmark)
    return [
        models.BookmarkedGuide(
            summary=summaries[guide],
            sections=_bookmarked_sections(
                bookmarks,
                sections.get(guide, models.GuideSections({})),
                first_paragraphs.get(guide, {}),
            ),
        )
        for guide, bookmarks in by_guide.items()
        if guide in summaries
    ]


def _bookmarked_sections(
    bookmarks: Sequence[models.Bookmark],
    sections: models.GuideSections,
    first_paragraphs: Mapping[str, str | None],
) -> tuple[models.BookmarkedSection, ...]:
    """A guide's bookmarked sections in the order the guide has them; one it no
    longer has, last."""
    in_order = sorted(bookmarks, key=lambda b: sections.order_of(b.section_id))
    placed = [
        (bookmark, sections.anchor_of(bookmark.section_id)) for bookmark in in_order
    ]
    return tuple(
        models.BookmarkedSection(
            section_id=bookmark.section_id,
            anchor=anchor,
            heading=bookmark.heading,
            first_paragraph=None if anchor is None else first_paragraphs.get(anchor),
        )
        for bookmark, anchor in placed
    )


async def _first_paragraphs(
    database: pymongo.asynchronous.database.AsyncDatabase,
    guides: Iterable[guidance_ids.DocumentId],
    s3_client: Any,
) -> dict[guidance_ids.DocumentId, dict[str, str | None]]:
    """Each guide's sections by anchor, with the first paragraph under each, as its
    latest version has them; none for a guide whose content is not there."""
    latest = await guidance_records.latest_versions_of(database, list(guides))
    read = await asyncio.gather(
        *(
            asyncio.to_thread(_first_paragraphs_at, version.content_url, s3_client)
            for version in latest.values()
        )
    )
    return dict(zip(latest, read, strict=True))


def _first_paragraphs_at(content_url: str, s3_client: Any) -> dict[str, str | None]:
    """The first paragraph of each section of the content stored at `content_url`,
    by the section's anchor; none if nothing is stored there."""
    stored = store.read(content_url, s3_client=s3_client)
    if stored is None:
        return {}
    document = reader.from_markdown(stored.decode("utf-8"))
    of_section = anchors.of_document(document.sections)
    return {
        of_section[id(section)]: paragraphs.first_of(section.content)
        for section in document.sections
    }


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
