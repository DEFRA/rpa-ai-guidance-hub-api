"""The hub's own searches, where the contract tests do not reach.

All fixture text is invented.
"""

from __future__ import annotations

from app.guidance import ids
from app.search import in_hub, title_search

POND_SURVEY = "OHF Pond Survey"


class TestFindingGuidesByTitleInTheHub:
    async def test_gives_guides_with_the_same_title_in_order_of_their_ids(self):
        """As the search service does, so that the order does not change between
        searches, whatever order the guides are read in."""
        first, second = sorted((ids.new_document_id(), ids.new_document_id()), key=str)

        async def read() -> list[title_search.GuideTitle]:
            return [
                title_search.GuideTitle(second, POND_SURVEY),
                title_search.GuideTitle(first, POND_SURVEY),
            ]

        found = await in_hub.InHubTitleSearch(read).like("pond survey", limit=8)

        assert [guide.guide_id for guide in found] == [first, second]

    async def test_finds_no_untitled_guide_for_no_words(self):
        """With no words on either side, there is nothing to compare, not a perfect
        likeness."""

        async def read() -> list[title_search.GuideTitle]:
            return [title_search.GuideTitle(ids.new_document_id(), "")]

        assert await in_hub.InHubTitleSearch(read).like(" – ", limit=8) == ()
