"""The links to SharePoint Word documents in each guide's latest version, one record per
guide in `guide_links`, so the guides that link to a document can be found by its
canonical link.

Only a link that `link_targets` finds names a document for certain is kept: a named
Word document, or a document by its SharePoint Document ID (`NAMING_A_DOCUMENT`). Any
other link cannot be matched to a document for certain.

A guide's record is replaced whole, in one write, so a reader never sees two versions'
links, and two saves cannot each remove the other's. It holds the links of the version
saved last by time, which is the guide's latest (`conversion._saved_at`): a version
saved earlier changes nothing, whenever it is kept. A guide whose latest version has no
such links has a record with none, so that no earlier version can put its links back.

**Derived data.** Everything here is read from the version's Markdown and can be read
again.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Any

import pymongo.errors

from app.common import mongo_timeouts
from app.guidance import ids, link_targets, models, reading, section_ids
from app.guidance.documents import store

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pymongo.asynchronous.database

GUIDE_LINKS = "guide_links"

_VERSION_ID = "versionId"
_SAVED_AT = "savedAt"
_LINKS = "links"
_CANONICAL_LINK = "canonicalLink"


@dataclasses.dataclass(frozen=True)
class GuideLink:
    """One link to a document in a guide's latest version: where it is (its place
    among all the guide's links; its section's id, None before the first section,
    and its order there), its words, the link as written, its canonical link and the
    file it names, None for a Document ID link that names none."""

    guide_id: ids.DocumentId
    version_id: ids.VersionId
    position: int
    section_id: ids.SectionId | None
    order: int
    text: str
    source_link: str
    canonical_link: str
    file_name: str | None

    def to_entry(self) -> dict[str, Any]:
        """The link in its guide's record, which holds its guide and version."""
        return {
            "position": self.position,
            "sectionId": self.section_id,
            "order": self.order,
            "text": self.text,
            "sourceLink": self.source_link,
            _CANONICAL_LINK: self.canonical_link,
            "fileName": self.file_name,
        }

    @classmethod
    def from_entry(
        cls, record: Mapping[str, Any], entry: Mapping[str, Any]
    ) -> GuideLink:
        return cls(
            guide_id=record["_id"],
            version_id=record[_VERSION_ID],
            position=entry["position"],
            section_id=entry["sectionId"],
            order=entry["order"],
            text=entry["text"],
            source_link=entry["sourceLink"],
            canonical_link=entry[_CANONICAL_LINK],
            file_name=entry["fileName"],
        )


def links_in(
    version: models.Version,
    markdown: str,
    sections: Mapping[str, ids.SectionId],
) -> list[GuideLink]:
    """The version's links that name a document, in order, each in its section, whose
    id `sections` gives by its anchor."""
    found: list[GuideLink] = []
    for position, link in enumerate(reading.links_of(markdown), start=1):
        target = link_targets.target_of(link.destination)
        if target.kind not in link_targets.NAMING_A_DOCUMENT:
            continue
        found.append(
            GuideLink(
                guide_id=version.document_id,
                version_id=version.id,
                position=position,
                section_id=None if link.section is None else sections.get(link.section),
                order=link.order,
                text=link.text,
                source_link=link.destination,
                canonical_link=str(target.canonical_link),
                file_name=target.file_name,
            )
        )
    return found


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """The index the guides that link to a canonical link are found by: an entry for
    each guide and each canonical link in it."""
    await database[GUIDE_LINKS].create_index([(f"{_LINKS}.{_CANONICAL_LINK}", 1)])


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def keep(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
    markdown: str,
) -> None:
    """Make the guide's links those of `version`, read from its Markdown, unless a
    version saved later is kept already."""
    collection = database[GUIDE_LINKS]
    links = links_in(version, markdown, await _section_ids(database, version, markdown))
    record = {
        "_id": version.document_id,
        _VERSION_ID: version.id,
        _SAVED_AT: version.created_at,
        _LINKS: [link.to_entry() for link in links],
    }
    no_later = {"_id": version.document_id, _SAVED_AT: {"$lte": version.created_at}}
    try:
        await collection.replace_one(no_later, record, upsert=True)
    except pymongo.errors.DuplicateKeyError:
        # The guide has a record the filter did not match: a later version's, or one
        # written since the filter was read. Replace it only if it is no later.
        await collection.replace_one(no_later, record)


async def keep_stored(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
    s3_client: Any,
) -> None:
    """`keep`, with the version's Markdown read from where it is stored.

    Raises:
        section_ids.ContentMissingError: if nothing is stored there.
    """
    stored = await asyncio.to_thread(
        store.read, version.content_url, s3_client=s3_client
    )
    if stored is None:
        message = f"No content at {version.content_url}, so its links cannot be kept"
        raise section_ids.ContentMissingError(message)
    await keep(database, version, stored.decode("utf-8"))


async def _section_ids(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
    markdown: str,
) -> Mapping[str, ids.SectionId]:
    """The version's section ids by anchor: those it keeps, or, for a guide's first
    version, which keeps none until they are asked for, given now."""
    if version.sections is not None:
        return {anchor: given.id for anchor, given in version.sections.items()}
    return await section_ids.of_content(database, version.id, markdown)


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def links_of(
    database: pymongo.asynchronous.database.AsyncDatabase,
    guide_id: ids.DocumentId,
) -> list[GuideLink]:
    """A guide's links, in the order they are in the guide; none for a guide whose
    links were never kept."""
    record = await database[GUIDE_LINKS].find_one({"_id": guide_id})
    if record is None:
        return []
    return [GuideLink.from_entry(record, entry) for entry in record[_LINKS]]


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def guides_linking_to(
    database: pymongo.asynchronous.database.AsyncDatabase,
    canonical_link: str,
) -> list[ids.DocumentId]:
    """The guides whose latest version links to `canonical_link`, each once, in id
    order: found through the index on it, so the time does not grow with the number of
    guides. Put in order here, as a sort in the query could read every guide instead."""
    cursor = database[GUIDE_LINKS].find(
        {f"{_LINKS}.{_CANONICAL_LINK}": canonical_link}, {"_id": 1}
    )
    return sorted([record["_id"] async for record in cursor])
