"""The links to SharePoint Word documents in each guide's latest version, in a real
MongoDB.

All fixture text is invented.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.guidance import guide_links, ids, models

FORM = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/Claims/The%20Claim%20Form.docx"
    "?d=w1&csf=1&web=1"
)
FORM_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/claims/the%20claim%20form.docx"
)
INTRODUCTION = ids.new_section_id()
CHECKS = ids.new_section_id()

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
    await guide_links.ensure_indexes(mongo_database)
    return mongo_database


def _version(guide: ids.DocumentId | None = None) -> models.Version:
    """A version whose sections are the two MARKDOWN has."""
    return models.Version(
        id=ids.new_version_id(),
        document_id=guide or ids.new_document_id(),
        content_url="s3://docs/content.md",
        created_at=dt.datetime.now(tz=dt.UTC),
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

    async def test_keep_the_link_as_written_and_its_canonical_form(self, database):
        version = _version()

        await guide_links.keep(database, version, MARKDOWN)

        form = (await guide_links.links_of(database, version.document_id))[1]
        assert form.id == f"{version.id}.2"
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
        second = _version(first.document_id)
        await guide_links.keep(database, first, MARKDOWN)

        await guide_links.keep(
            database, second, f"## 1 Introduction\n\n[the form]({FORM})"
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


class TestTheIndexes:
    async def test_find_a_canonical_links_guides_and_a_guides_links_in_order(
        self, database
    ):
        indexes = await database[guide_links.GUIDE_LINKS].index_information()

        keys = [index["key"] for index in indexes.values()]
        assert [("canonicalLink", 1), ("guideId", 1)] in keys
        assert [("guideId", 1), ("position", 1)] in keys
