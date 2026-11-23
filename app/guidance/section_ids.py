"""Each section's id: what anything pointing into a guide keeps, so that it can
follow its section from one version to the next while the section's anchor, its
number and words, stays its address.

A version's sections are given their ids the first time they are asked for, and
kept with the version from then on; when two ask at once, both read back whichever
was kept first. Every version is given them this way, however old: from its stored
content (`of_latest`), which is read only until they are kept. A new version's
sections are given the ids of the same sections in the version before
(`of_new_version`), written with the version, so what points into a section follows it.

A heading changes only with a new version, so a new version keeps each section's
heading beside its id (`headings_of`), for what points into a section to name it as it
reads now. A guide's first version keeps none: its headings are as each pin and
bookmark kept them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.common import mongo_timeouts
from app.guidance import ids, models, records, section_matching
from app.guidance.documents import reader, store
from app.guidance.parsing import anchors

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pymongo.asynchronous.database

    from app.guidance.parsing import models as parsing_models

_SECTION_IDS = models.SECTION_IDS


async def of_latest(
    database: pymongo.asynchronous.database.AsyncDatabase,
    latest: Mapping[ids.DocumentId, models.Version],
    s3_client: Any,
) -> dict[ids.DocumentId, dict[str, ids.SectionId]]:
    """Each guide's sections by anchor, in its order, with their ids, as its latest
    version (`latest`) has them: given now, from the version's stored content, if it
    has none yet. None for a guide whose content is not there, and none kept."""
    kept = await _kept(database, [version.id for version in latest.values()])

    found: dict[ids.DocumentId, dict[str, ids.SectionId]] = {}
    for guide, version in latest.items():
        given = kept.get(version.id)
        if given is None:
            at = await asyncio.to_thread(_anchors_at, version.content_url, s3_client)
            given = {} if at is None else await of_version(database, version.id, at)
        found[guide] = given
    return found


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def _kept(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_ids: Sequence[ids.VersionId],
) -> dict[ids.VersionId, dict[str, ids.SectionId]]:
    """The section ids already given, by version, for those of `version_ids` that have
    any. Timed on its own: `of_latest` also waits on S3, which is not Mongo's time."""
    return {
        found["_id"]: _by_anchor(found[_SECTION_IDS])
        async for found in database[records.VERSIONS].find(
            {"_id": {"$in": list(version_ids)}, _SECTION_IDS: {"$exists": True}},
            {_SECTION_IDS: 1},
        )
    }


def _anchors_at(content_url: str, s3_client: Any) -> list[str] | None:
    """The anchor of each section of the content stored at `content_url`, in its
    order; None if nothing is stored there."""
    sections = _sections_at(content_url, s3_client)
    return None if sections is None else _anchors_of(sections)


def _sections_at(
    content_url: str, s3_client: Any
) -> list[parsing_models.MarkdownSection] | None:
    """The sections of the content stored at `content_url`, in its order; None if
    nothing is stored there."""
    stored = store.read(content_url, s3_client=s3_client)
    if stored is None:
        return None
    return list(reader.from_markdown(stored.decode("utf-8")).sections)


def _anchors_of(sections: Sequence[parsing_models.MarkdownSection]) -> list[str]:
    of_section = anchors.of_document(list(sections))
    return [of_section[id(section)] for section in sections]


async def of_new_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    before: models.Version,
    content_url: str,
    s3_client: Any,
) -> dict[str, models.GivenSection] | None:
    """Each section of a new version, stored at `content_url` and not yet recorded, by
    its anchor in its order, with its heading and the id of the same section in the
    version `before` it (`carried_over`), or a new id. None where either content is not
    there: its sections are given ids when they are first asked for, as any version's
    are."""
    before_sections = await asyncio.to_thread(
        _sections_at, before.content_url, s3_client
    )
    after_sections = await asyncio.to_thread(_sections_at, content_url, s3_client)
    if before_sections is None or after_sections is None:
        return None
    before_ids = await of_version(database, before.id, _anchors_of(before_sections))
    carried = carried_over(before_sections, before_ids, after_sections)
    headings = dict(
        zip(
            _anchors_of(after_sections),
            (section.rendered_heading for section in after_sections),
            strict=True,
        )
    )
    return {
        anchor: models.GivenSection(section_id, headings[anchor])
        for anchor, section_id in carried.items()
    }


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def headings_of(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_ids: Sequence[ids.VersionId],
) -> dict[ids.VersionId, dict[ids.SectionId, str]]:
    """Each section's heading by its id, by version, for those of `version_ids` that
    keep them: each version after a guide's first."""
    found: dict[ids.VersionId, dict[ids.SectionId, str]] = {}
    async for version in database[records.VERSIONS].find(
        {"_id": {"$in": list(version_ids)}, f"{_SECTION_IDS}.heading": {"$ne": None}},
        {_SECTION_IDS: 1},
    ):
        found[version["_id"]] = {
            ids.SectionId(section["id"]): section["heading"]
            for section in version[_SECTION_IDS]
            if section.get("heading") is not None
        }
    return found


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
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


def carried_over(
    before: Sequence[parsing_models.MarkdownSection],
    before_ids: Mapping[str, ids.SectionId],
    after: Sequence[parsing_models.MarkdownSection],
) -> dict[str, ids.SectionId]:
    """Each section of a new version (`after`) by its anchor, in its order, with the
    id of the same section of the version before (`before`, whose ids by anchor are
    `before_ids`); a new id for a new section."""
    before_anchors = anchors.of_document(list(before))
    after_anchors = anchors.of_document(list(after))
    earlier = section_matching.earlier_sections(before, after)
    return {
        after_anchors[id(section)]: (
            ids.new_section_id()
            if at is None
            else before_ids[before_anchors[id(before[at])]]
        )
        for section, at in zip(after, earlier, strict=True)
    }


def _by_anchor(kept: Sequence[Mapping[str, Any]]) -> dict[str, ids.SectionId]:
    return {section["anchor"]: ids.SectionId(section["id"]) for section in kept}
