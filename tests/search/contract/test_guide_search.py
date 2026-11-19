"""What any implementation of search in one guide must do: the hub's contract with it.

Each test runs once for each implementation the hub can switch to. The words are
matched as a keyword search, to start with; a different search can change these
tests. What they must keep is what the guide page needs: each match placed in its
section's text as a reader sees it (`reading.sections_of`), with the words it covers.

The tests run against the search service only when SEARCH_SERVICE_FOR_CONTRACT_TESTS
gives the address of one that is running, as the local stack's is at
http://localhost:8086. A test it cannot pass yet is an expected failure there, with
the reason: that list is what the search service must do before a search moves to it.

All fixture text is invented.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import os
from typing import ClassVar, Protocol

import pytest

from app.common import http_client
from app.guidance import ids
from app.search import guide_search, in_hub, search_service

Match = guide_search.Match
SectionMatches = guide_search.SectionMatches

SEARCH_SERVICE_ADDRESS = "SEARCH_SERVICE_FOR_CONTRACT_TESTS"


class _Searching(Protocol):
    """An implementation, and how a guide is stored where it searches."""

    search: guide_search.GuideSearch

    # The tests it cannot pass yet, by name, each with the reason.
    expected_failures: ClassVar[dict[str, str]]

    async def stored(self, markdown: str) -> ids.DocumentId: ...

    def version_of(self, guide_id: ids.DocumentId) -> ids.VersionId: ...


@dataclasses.dataclass
class _InTheHub:
    expected_failures: ClassVar[dict[str, str]] = {}

    guides: dict[ids.DocumentId, in_hub.Guide] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        self.search = in_hub.InHubGuideSearch(self._read)

    async def stored(self, markdown: str) -> ids.DocumentId:
        guide_id = ids.new_document_id()
        self.guides[guide_id] = in_hub.Guide(ids.new_version_id(), markdown)
        return guide_id

    def version_of(self, guide_id: ids.DocumentId) -> ids.VersionId:
        return self.guides[guide_id].version_id

    async def _read(self, guide_id: ids.DocumentId) -> in_hub.Guide | None:
        return self.guides.get(guide_id)


@dataclasses.dataclass
class _InTheSearchService:
    """Publishes each guide to the search service, and waits until it can be searched."""

    expected_failures: ClassVar[dict[str, str]] = {
        "test_reads_a_quote_in_a_list_a_paragraph_to_a_line_without_its_marks": (
            "It keeps a quote's marks when the quote is in a list, so each match after "
            "them is placed too far on"
        ),
    }

    seconds_to_wait: ClassVar[float] = 30
    seconds_between_tries: ClassVar[float] = 0.25

    address: str = dataclasses.field(
        default_factory=lambda: os.environ[SEARCH_SERVICE_ADDRESS]
    )
    versions: dict[ids.DocumentId, ids.VersionId] = dataclasses.field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.search = search_service.SearchServiceGuideSearch(self.address)

    async def stored(self, markdown: str) -> ids.DocumentId:
        guide_id = ids.new_document_id()
        version_id = ids.new_version_id()
        async with http_client.create_async_client() as client:
            response = await client.put(
                f"{self.address}/documents/{guide_id}/versions/{version_id}",
                json={
                    "sourceType": "guide",
                    "status": "published",
                    "occurredAt": dt.datetime.now(dt.UTC).isoformat(),
                    "content": {"markdown": markdown},
                    "metadata": {"guideTitle": "Contract test guide"},
                },
            )
        response.raise_for_status()
        self.versions[guide_id] = version_id
        await self._until_searchable(guide_id)
        return guide_id

    def version_of(self, guide_id: ids.DocumentId) -> ids.VersionId:
        return self.versions[guide_id]

    async def _until_searchable(self, guide_id: ids.DocumentId) -> None:
        tries = int(self.seconds_to_wait / self.seconds_between_tries)
        for _ in range(tries):
            if await self.search.in_guide(guide_id, "any") is not None:
                return
            await asyncio.sleep(self.seconds_between_tries)
        pytest.fail(
            f"The search service did not serve guide {guide_id} in {self.seconds_to_wait} seconds"
        )


@pytest.fixture(
    params=[
        _InTheHub,
        pytest.param(
            _InTheSearchService,
            marks=pytest.mark.skipif(
                SEARCH_SERVICE_ADDRESS not in os.environ,
                reason=f"{SEARCH_SERVICE_ADDRESS} does not give a search service",
            ),
        ),
    ],
    ids=["in the hub", "in the search service"],
)
def searching(request: pytest.FixtureRequest) -> _Searching:
    implementation: type[_Searching] = request.param
    reason = implementation.expected_failures.get(request.function.__name__)
    if reason is not None:
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True))
    return implementation()


class TestSearchingAGuide:
    async def test_finds_the_words_anywhere_in_a_word_in_any_case_saying_where(
        self, searching
    ):
        guide = await searching.stored(
            "# Claims\n\n## 1 Paying\n\nRepayment, then payment."
        )

        found = await searching.search.in_guide(guide, "pay")

        assert found.sections == (
            SectionMatches(
                "1-paying",
                (Match(2, 5, "Pay"), Match(11, 14, "pay"), Match(25, 28, "pay")),
            ),
        )

    async def test_reads_each_section_as_a_reader_sees_it(self, searching):
        markdown = "## Forms\n\nUse the **claim** [form](https://example.com/pay)."
        guide = await searching.stored(markdown)

        in_the_words = await searching.search.in_guide(guide, "claim form")
        in_the_address = await searching.search.in_guide(guide, "pay")

        assert (in_the_words.sections, in_the_address.sections) == (
            (SectionMatches("forms", (Match(14, 24, "claim form"),)),),
            (),
        )

    async def test_reads_a_quote_in_a_list_a_paragraph_to_a_line_without_its_marks(
        self, searching
    ):
        guide = await searching.stored(
            "## Notes\n\n- Add this note:\n\n  > Dear claimant,\n  >\n  > Pay the claim.\n\nPay late."
        )

        found = await searching.search.in_guide(guide, "pay")

        assert found.sections == (
            SectionMatches("notes", (Match(36, 39, "Pay"), Match(51, 54, "Pay"))),
        )

    async def test_finds_a_phrase_across_a_wrapped_line_not_from_one_block_into_the_next(
        self, searching
    ):
        guide = await searching.stored(
            "## Notes\n\nPay the claim\nin full.\n\nA claim\n\nin time."
        )

        found = await searching.search.in_guide(guide, "claim in")

        assert found.sections == (
            SectionMatches("notes", (Match(14, 22, "claim in"),)),
        )

    async def test_gives_the_sections_holding_the_words_in_order_the_introduction_first(
        self, searching
    ):
        guide = await searching.stored(
            "# Claims\n\nPay first.\n\n## 1 Notes\n\nNone here.\n\n## 2 Late\n\nPay late.",
        )

        found = await searching.search.in_guide(guide, "pay")

        assert found.sections == (
            SectionMatches(None, (Match(0, 3, "Pay"),)),
            SectionMatches("2-late", (Match(7, 10, "Pay"),)),
        )

    async def test_names_the_version_it_searched(self, searching):
        guide = await searching.stored("## Notes\n\nPay.")

        found = await searching.search.in_guide(guide, "pay")

        assert found.version_id == searching.version_of(guide)

    async def test_finds_nothing_for_no_words(self, searching):
        guide = await searching.stored("## Notes\n\nPay.")

        found = await searching.search.in_guide(guide, "  ")

        assert found.sections == ()

    async def test_finds_no_guide_that_is_not_stored(self, searching):
        assert await searching.search.in_guide(ids.new_document_id(), "pay") is None
