"""The links to SharePoint Word documents in each guide's latest version, in a real
MongoDB.

All fixture text is invented.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt

import pytest

from app.guidance import guide_links, ids, models, records

FORM = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/Claims/The%20Claim%20Form.docx"
    "?d=w1&csf=1&web=1"
)
FORM_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/claims/the%20claim%20form.docx"
)
INTRODUCTION = ids.new_section_id()
CHECKS = ids.new_section_id()
SAVED = dt.datetime(2026, 11, 26, 9, 0, tzinfo=dt.UTC)

MARKDOWN = (
    "[the guide index](https://example.sharepoint.com/:w:/r/teams/Team1234/Index.docx)\n\n"
    "# Claims guide\n\n"
    "## 1 Introduction\n\n"
    f"Fill in [[**the claim form**]{{.red}}]({FORM}), then see [section 2](#2-checks).\n\n"
    "## 2 Checks\n\n"
    "- [Draft Letter](file://share.example.gov.uk/Shared/Draft%20Letter)\n"
    "- [the rules](https://www.gov.uk/rules)\n"
    "- [the claims folder](https://example.sharepoint.com/:f:/r/teams/Team1234/Claims)\n"
    f"- [the form again]({FORM})\n"
)


@pytest.fixture
async def database(mongo_database):
    """With no guide's links kept, as one case's guides are another's matches."""
    await mongo_database[guide_links.GUIDE_LINKS].delete_many({})
    await guide_links.ensure_indexes(mongo_database)
    return mongo_database


def _version(
    guide: ids.DocumentId | None = None, at: dt.datetime = SAVED
) -> models.Version:
    """A version whose sections are the two MARKDOWN has, saved `at`."""
    return models.Version(
        id=ids.new_version_id(),
        document_id=guide or ids.new_document_id(),
        content_url="s3://docs/content.md",
        created_at=at,
        sections={
            "1-introduction": models.GivenSection(INTRODUCTION),
            "2-checks": models.GivenSection(CHECKS),
        },
    )


class TestAVersionsLinks:
    async def test_are_those_to_a_named_word_document_with_where_each_is(
        self, database
    ):
        version = _version()

        await guide_links.keep(database, version, MARKDOWN)

        links = await guide_links.links_of(database, version.document_id)
        assert [
            (link.position, link.section_id, link.order, link.text) for link in links
        ] == [
            (1, None, 1, "the guide index"),
            (2, INTRODUCTION, 1, "the claim form"),
            (7, CHECKS, 4, "the form again"),
        ]

    async def test_of_a_first_version_keep_the_section_ids_it_is_given_now(
        self, database
    ):
        """A guide's first version has no section ids until they are asked for."""
        version = dataclasses.replace(_version(), sections=None)
        await database[records.VERSIONS].insert_one(version.to_document())

        await guide_links.keep(database, version, MARKDOWN)

        kept = models.Version.from_document(
            await database[records.VERSIONS].find_one({"_id": version.id})
        )
        links = await guide_links.links_of(database, version.document_id)
        assert kept.sections is not None, "the version was given no section ids"
        assert [link.section_id for link in links] == [
            None,
            kept.sections["1-introduction"].id,
            kept.sections["2-checks"].id,
        ]

    async def test_keep_the_link_as_written_and_its_canonical_form(self, database):
        version = _version()

        await guide_links.keep(database, version, MARKDOWN)

        form = (await guide_links.links_of(database, version.document_id))[1]
        assert form.guide_id == version.document_id
        assert form.version_id == version.id
        assert form.source_link == FORM
        assert form.canonical_link == FORM_CANONICAL
        assert form.file_name == "The Claim Form.docx"

    async def test_are_recorded_once_however_often_the_version_is_kept(self, database):
        version = _version()

        await guide_links.keep(database, version, MARKDOWN)
        await guide_links.keep(database, version, MARKDOWN)

        assert len(await guide_links.links_of(database, version.document_id)) == 3

    async def test_are_none_for_a_guide_without_such_links(self, database):
        version = _version()

        await guide_links.keep(
            database,
            version,
            "## 1 Introduction\n\n[the rules](https://www.gov.uk/rules)",
        )

        assert await guide_links.links_of(database, version.document_id) == []


class TestANewVersion:
    async def test_replaces_the_guides_links_with_its_own(self, database):
        first = _version()
        second = _version(first.document_id, SAVED + dt.timedelta(minutes=1))
        await guide_links.keep(database, first, MARKDOWN)

        await guide_links.keep(
            database, second, f"## 1 Introduction\n\n[the form]({FORM})"
        )

        links = await guide_links.links_of(database, first.document_id)
        assert [(link.version_id, link.text) for link in links] == [
            (second.id, "the form")
        ]

    async def test_keeps_its_links_when_an_earlier_version_is_kept_after_it(
        self, database
    ):
        """The newest by time is the guide's latest, whichever is kept last."""
        first = _version()
        second = _version(first.document_id, SAVED + dt.timedelta(minutes=1))
        await guide_links.keep(
            database, second, f"## 1 Introduction\n\n[the form]({FORM})"
        )

        await guide_links.keep(database, first, MARKDOWN)

        links = await guide_links.links_of(database, first.document_id)
        assert [(link.version_id, link.text) for link in links] == [
            (second.id, "the form")
        ]

    async def test_keeps_its_links_when_kept_at_the_same_time_as_an_earlier_one(
        self, database
    ):
        first = _version()
        second = _version(first.document_id, SAVED + dt.timedelta(minutes=1))

        await asyncio.gather(
            guide_links.keep(
                database, second, f"## 1 Introduction\n\n[the form]({FORM})"
            ),
            guide_links.keep(database, first, MARKDOWN),
        )

        links = await guide_links.links_of(database, first.document_id)
        assert [(link.version_id, link.text) for link in links] == [
            (second.id, "the form")
        ]

    async def test_leaves_other_guides_links_alone(self, database):
        other = _version()
        version = _version()
        await guide_links.keep(database, other, MARKDOWN)

        await guide_links.keep(database, version, MARKDOWN)
        await guide_links.keep(database, _version(version.document_id), "No links.")

        assert len(await guide_links.links_of(database, other.document_id)) == 3


class TestTheGuidesThatLinkToADocument:
    async def test_are_each_guide_whose_latest_version_links_to_it_once(self, database):
        twice = _version()
        once = _version()
        await guide_links.keep(database, twice, MARKDOWN)
        await guide_links.keep(
            database, once, f"## 1 Introduction\n\n[the form]({FORM})"
        )
        await guide_links.keep(database, _version(), "## 1 Introduction\n\nNo links.")

        found = await guide_links.guides_linking_to(database, FORM_CANONICAL)

        assert found == sorted([twice.document_id, once.document_id])

    async def test_are_found_through_the_index_on_the_canonical_link(self, database):
        """So the time does not grow with the number of guides."""
        await guide_links.keep(database, _version(), MARKDOWN)
        before = await _uses_of_the_canonical_link_index(database)

        await guide_links.guides_linking_to(database, FORM_CANONICAL)

        assert await _uses_of_the_canonical_link_index(database) == before + 1


async def _uses_of_the_canonical_link_index(database) -> int:
    """How many operations have used the index on each link's canonical link."""
    cursor = await database[guide_links.GUIDE_LINKS].aggregate([{"$indexStats": {}}])
    async for index in cursor:
        if index["key"] == {"links.canonicalLink": 1}:
            return index["accesses"]["ops"]
    message = "No index on links.canonicalLink"
    raise AssertionError(message)
