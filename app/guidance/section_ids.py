"""Each section's id: what anything pointing into a guide keeps, so that it can
follow its section from one version to the next while the section's anchor, its
number and words, stays its address.

A version's sections are given their ids the first time they are asked for, and
kept with the version from then on; when two ask at once, both read back whichever
was kept first. Every version is given them this way, however old: from its stored
content (`of_latest`), which is read only until they are kept.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.guidance import ids, records
from app.guidance.documents import reader, store
from app.guidance.parsing import anchors

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pymongo.asynchronous.database

    from app.guidance import models

# Where a version record keeps its sections' ids, in order: `{anchor, id}` each.
_SECTION_IDS = "sectionIds"


async def of_latest(
    database: pymongo.asynchronous.database.AsyncDatabase,
    latest: Mapping[ids.DocumentId, models.Version],
    s3_client: Any,
) -> dict[ids.DocumentId, dict[str, ids.SectionId]]:
    """Each guide's sections by anchor, in its order, with their ids, as its latest
    version (`latest`) has them: given now, from the version's stored content, if it
    has none yet. None for a guide whose content is not there, and none kept."""
    versions = database[records.VERSIONS]
    kept = {
        found["_id"]: _by_anchor(found[_SECTION_IDS])
        async for found in versions.find(
            {
                "_id": {"$in": [version.id for version in latest.values()]},
                _SECTION_IDS: {"$exists": True},
            },
            {_SECTION_IDS: 1},
        )
    }

    found: dict[ids.DocumentId, dict[str, ids.SectionId]] = {}
    for guide, version in latest.items():
        given = kept.get(version.id)
        if given is None:
            at = await asyncio.to_thread(_anchors_at, version.content_url, s3_client)
            given = {} if at is None else await of_version(database, version.id, at)
        found[guide] = given
    return found


def _anchors_at(content_url: str, s3_client: Any) -> list[str] | None:
    """The anchor of each section of the content stored at `content_url`, in its
    order; None if nothing is stored there."""
    stored = store.read(content_url, s3_client=s3_client)
    if stored is None:
        return None
    document = reader.from_markdown(stored.decode("utf-8"))
    of_section = anchors.of_document(document.sections)
    return [of_section[id(section)] for section in document.sections]


async def of_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_id: ids.VersionId,
    in_order: Sequence[str],
) -> dict[str, ids.SectionId]:
    """Each section's id by its anchor, given now, to the anchors `in_order`, if the
    version has none yet; none for a version that is not there."""
    versions = database[records.VERSIONS]
    found = await versions.find_one({"_id": version_id}, {_SECTION_IDS: 1})
    if found is not None and _SECTION_IDS not in found:
        given = [{"anchor": anchor, "id": ids.new_section_id()} for anchor in in_order]
        await versions.update_one(
            {"_id": version_id, _SECTION_IDS: {"$exists": False}},
            {"$set": {_SECTION_IDS: given}},
        )
        found = await versions.find_one({"_id": version_id}, {_SECTION_IDS: 1})
    return {} if found is None else _by_anchor(found[_SECTION_IDS])


def _by_anchor(kept: Sequence[Mapping[str, Any]]) -> dict[str, ids.SectionId]:
    return {section["anchor"]: ids.SectionId(section["id"]) for section in kept}
