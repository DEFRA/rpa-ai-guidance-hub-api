"""What any implementation of search in one guide must do: the hub's contract with it.

Each test runs once for each implementation the hub can switch to. The words are
matched as a keyword search, to start with; a different search can change these
tests. What they must keep is what the guide page needs: each match placed in its
section's text as a reader sees it (`reading.sections_of`), with the words it covers.

All fixture text is invented.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

import pytest

from app.guidance import ids
from app.search import guide_search, in_hub

Match = guide_search.Match
SectionMatches = guide_search.SectionMatches


class _Searching(Protocol):
    """An implementation, and how a guide is stored where it searches."""

    search: guide_search.GuideSearch

    async def stored(self, title: str, markdown: str) -> ids.DocumentId: ...

    def version_of(self, guide_id: ids.DocumentId) -> ids.VersionId: ...


@dataclasses.dataclass
class _InTheHub:
    guides: dict[ids.DocumentId, in_hub.Guide] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        self.search = in_hub.InHubGuideSearch(self._read)

    async def stored(self, title: str, markdown: str) -> ids.DocumentId:
        guide_id = ids.new_document_id()
        self.guides[guide_id] = in_hub.Guide(ids.new_version_id(), title, markdown)
        return guide_id

    def version_of(self, guide_id: ids.DocumentId) -> ids.VersionId:
        return self.guides[guide_id].version_id

    async def _read(self, guide_id: ids.DocumentId) -> in_hub.Guide | None:
        return self.guides.get(guide_id)


@pytest.fixture(params=[_InTheHub], ids=["in the hub"])
def searching(request: pytest.FixtureRequest) -> _Searching:
    implementation: type[_Searching] = request.param
    return implementation()


class TestSearchingAGuide:
    async def test_finds_the_words_anywhere_in_a_word_in_any_case_saying_where(
        self, searching
    ):
        guide = await searching.stored(
            "Claims", "# Claims\n\n## 1 Paying\n\nRepayment, then payment."
        )

        found = await searching.search.in_guide(guide, "pay")

        assert found.sections == (
            SectionMatches(
                "1-paying",
                (Match(2, 5, "Pay"), Match(11, 14, "pay"), Match(25, 28, "pay")),
            ),
        )

    async def test_finds_the_words_in_the_guides_title_apart_from_its_sections(
        self, searching
    ):
        guide = await searching.stored(
            "Overpayments and repayments", "# Claims\n\n## Notes\n\nNone here."
        )

        found = await searching.search.in_guide(guide, "pay")

        assert (found.title, found.sections) == (
            (Match(4, 7, "pay"), Match(19, 22, "pay")),
            (),
        )

    async def test_reads_each_section_as_a_reader_sees_it(self, searching):
        markdown = "## Forms\n\nUse the **claim** [form](https://example.com/pay)."
        guide = await searching.stored("Claims", markdown)

        in_the_words = await searching.search.in_guide(guide, "claim form")
        in_the_address = await searching.search.in_guide(guide, "pay")

        assert (in_the_words.sections, in_the_address.sections) == (
            (SectionMatches("forms", (Match(14, 24, "claim form"),)),),
            (),
        )

    async def test_finds_a_phrase_across_a_wrapped_line_not_from_one_block_into_the_next(
        self, searching
    ):
        guide = await searching.stored(
            "Claims", "## Notes\n\nPay the claim\nin full.\n\nA claim\n\nin time."
        )

        found = await searching.search.in_guide(guide, "claim in")

        assert found.sections == (
            SectionMatches("notes", (Match(14, 22, "claim in"),)),
        )

    async def test_gives_the_sections_holding_the_words_in_order_the_introduction_first(
        self, searching
    ):
        guide = await searching.stored(
            "Claims",
            "# Claims\n\nPay first.\n\n## 1 Notes\n\nNone here.\n\n## 2 Late\n\nPay late.",
        )

        found = await searching.search.in_guide(guide, "pay")

        assert found.sections == (
            SectionMatches(None, (Match(0, 3, "Pay"),)),
            SectionMatches("2-late", (Match(7, 10, "Pay"),)),
        )

    async def test_names_the_version_it_searched(self, searching):
        guide = await searching.stored("Claims", "## Notes\n\nPay.")

        found = await searching.search.in_guide(guide, "pay")

        assert found.version_id == searching.version_of(guide)

    async def test_finds_nothing_for_no_words(self, searching):
        guide = await searching.stored("Claims", "## Notes\n\nPay.")

        found = await searching.search.in_guide(guide, "  ")

        assert (found.title, found.sections) == ((), ())

    async def test_finds_no_guide_that_is_not_stored(self, searching):
        assert await searching.search.in_guide(ids.new_document_id(), "pay") is None
