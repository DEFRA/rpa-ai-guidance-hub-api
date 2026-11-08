"""Each section's id: what anything pointing into a guide keeps, so that it can
follow its section from one version to the next while the section's anchor, its
number and words, stays its address.

A version's sections are given their ids the first time they are asked for, and
kept with the version from then on; when two ask at once, both read back whichever
was kept first. Every version is given them this way, however old.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.guidance import ids, records

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pymongo.asynchronous.database

# Where a version record keeps its sections' ids, in order: `{anchor, id}` each.
_SECTION_IDS = "sectionIds"


async def of_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_id: ids.VersionId,
    anchors: Sequence[str],
) -> dict[str, ids.SectionId]:
    """Each section's id by its anchor, given now if the version has none yet; none
    for a version that is not there."""
    versions = database[records.VERSIONS]
    found = await versions.find_one({"_id": version_id}, {_SECTION_IDS: 1})
    if found is not None and _SECTION_IDS not in found:
        given = [{"anchor": anchor, "id": ids.new_section_id()} for anchor in anchors]
        await versions.update_one(
            {"_id": version_id, _SECTION_IDS: {"$exists": False}},
            {"$set": {_SECTION_IDS: given}},
        )
        found = await versions.find_one({"_id": version_id}, {_SECTION_IDS: 1})
    return {} if found is None else _by_anchor(found[_SECTION_IDS])


def _by_anchor(kept: Sequence[Mapping[str, Any]]) -> dict[str, ids.SectionId]:
    return {section["anchor"]: ids.SectionId(section["id"]) for section in kept}
