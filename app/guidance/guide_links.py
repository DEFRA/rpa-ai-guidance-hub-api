"""The links to SharePoint Word documents in each guide's latest version, one record
per link in `guide_links`, so the guides that link to a document can be found by its
canonical link.

Only a link that `link_targets` finds to be a named Word document is recorded: any
other link cannot be matched to a document for certain. A record's id is its version's
and its place among all the guide's links, `<version id>.<position>`, so keeping a
version twice writes the same records again rather than more of them.

**Derived data.** Everything here is read from the version's Markdown and can be read
again. A guide's records are its latest version's: keeping a new version replaces
them.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pymongo

from app.common import mongo_timeouts
from app.guidance import ids, link_targets, models, reading, section_ids

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pymongo.asynchronous.database

GUIDE_LINKS = "guide_links"

_GUIDE_ID = "guideId"
_VERSION_ID = "versionId"
_POSITION = "position"
_CANONICAL_LINK = "canonicalLink"


@dataclasses.dataclass(frozen=True)
class GuideLink:
    """One link to a named Word document in a guide's latest version: where it is
    (its section's id, None before the first section, and its order there), its
    words, the link as written, its canonical link and the file it names."""

    id: str
    guide_id: ids.DocumentId
    version_id: ids.VersionId
    position: int
    section_id: ids.SectionId | None
    order: int
    text: str
    source_link: str
    canonical_link: str
    file_name: str

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            _GUIDE_ID: self.guide_id,
            _VERSION_ID: self.version_id,
            _POSITION: self.position,
            "sectionId": self.section_id,
            "order": self.order,
            "text": self.text,
            "sourceLink": self.source_link,
            _CANONICAL_LINK: self.canonical_link,
            "fileName": self.file_name,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> GuideLink:
        return cls(
            id=document["_id"],
            guide_id=document[_GUIDE_ID],
            version_id=document[_VERSION_ID],
            position=document[_POSITION],
            section_id=document["sectionId"],
            order=document["order"],
            text=document["text"],
            source_link=document["sourceLink"],
            canonical_link=document[_CANONICAL_LINK],
            file_name=document["fileName"],
        )


def links_in(
    version: models.Version,
    markdown: str,
    sections: Mapping[str, ids.SectionId],
) -> list[GuideLink]:
    """The version's links to named Word documents, in order, each in its section,
    whose id `sections` gives by its anchor."""
    found: list[GuideLink] = []
    for position, link in enumerate(reading.links_of(markdown), start=1):
        target = link_targets.target_of(link.destination)
        if target.kind != link_targets.Kind.WORD_DOCUMENT:
            continue
        found.append(
            GuideLink(
                id=f"{version.id}.{position}",
                guide_id=version.document_id,
                version_id=version.id,
                position=position,
                section_id=None if link.section is None else sections.get(link.section),
                order=link.order,
                text=link.text,
                source_link=link.destination,
                canonical_link=str(target.canonical_link),
                file_name=str(target.file_name),
            )
        )
    return found


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """The indexes the records are read by: the guides a canonical link is in, and a
    guide's links in order."""
    await database[GUIDE_LINKS].create_index([(_CANONICAL_LINK, 1), (_GUIDE_ID, 1)])
    await database[GUIDE_LINKS].create_index([(_GUIDE_ID, 1), (_POSITION, 1)])


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def keep(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
    markdown: str,
) -> None:
    """Make the guide's links those of `version`, read from its Markdown."""
    collection = database[GUIDE_LINKS]
    links = links_in(version, markdown, await _section_ids(database, version, markdown))
    if links:
        await collection.bulk_write(
            [
                pymongo.ReplaceOne({"_id": link.id}, link.to_document(), upsert=True)
                for link in links
            ],
            ordered=False,
        )
    await collection.delete_many(
        {_GUIDE_ID: version.document_id, _VERSION_ID: {"$ne": version.id}}
    )


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
    """A guide's links, in the order they are in the guide."""
    cursor = database[GUIDE_LINKS].find({_GUIDE_ID: guide_id}).sort(_POSITION, 1)
    return [GuideLink.from_document(document) async for document in cursor]
