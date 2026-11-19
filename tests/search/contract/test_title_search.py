"""What any implementation of finding guides by title must do: the hub's contract with it.

Each test runs once for each implementation the hub can switch to. A guide is found by
a title like its own: with a typo, half a word, its words in another order, only some
of them, or more. The guide meant comes first.

All fixture text is invented.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

import pytest

from app.guidance import ids
from app.search import in_hub, title_search

GuideTitle = title_search.GuideTitle

BREACHES = "WG Domestic Revenue and Capital Breaches and Reductions"
CHECKLIST = (
    "WG Revenue Claims Final Payment Checklist – Supervisory Checks Fill in this "
    "checklist on screen. — Use the note box at the end to explain a check's result."
)
TITLES = (
    "WG MA Claim – Revenue Option Claim Rule at Signoff",
    "WG MA Claim – Organic Status Error at Signoff",
    "WG MA Claim - Claim Refresh Signoff Check",
    "WG Revenue Claims Processing to Final Payment Guide",
    "Final Payment Case Review Guide",
    BREACHES,
    "2019 Field Mapper Basic Navigation Guide",
    "Contact Desk Basic Navigation and Admin",
    "Final Payment Awaiting Approval QC Proforma",
    "WG Revenue Claims Supervisory Checks Guidance",
    CHECKLIST,
)


class _Finding(Protocol):
    """An implementation, and how guides are stored where it finds them."""

    search: title_search.TitleSearch

    async def stored(self, titles: tuple[str, ...]) -> dict[str, ids.DocumentId]: ...


@dataclasses.dataclass
class _InTheHub:
    guides: list[GuideTitle] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        self.search = in_hub.InHubTitleSearch(self._read)

    async def stored(self, titles: tuple[str, ...]) -> dict[str, ids.DocumentId]:
        kept = {title: ids.new_document_id() for title in titles}
        self.guides.extend(
            GuideTitle(guide_id, title) for title, guide_id in kept.items()
        )
        return kept

    async def _read(self) -> list[GuideTitle]:
        return self.guides


@pytest.fixture(params=[_InTheHub], ids=["in the hub"])
def finding(request: pytest.FixtureRequest) -> _Finding:
    implementation: type[_Finding] = request.param
    return implementation()


class TestFindingAGuideByTitle:
    @pytest.mark.parametrize(
        ("words", "title"),
        [
            pytest.param("reducions", BREACHES, id="with a typo"),
            pytest.param("reduct", BREACHES, id="by half a word"),
            pytest.param(
                "signoff claim rule revenue",
                "WG MA Claim – Revenue Option Claim Rule at Signoff",
                id="with its words in another order",
            ),
            pytest.param(
                "field mapper navigation",
                "2019 Field Mapper Basic Navigation Guide",
                id="by some of its words",
            ),
            pytest.param(
                "final payment case",
                "Final Payment Case Review Guide",
                id="by the words that start it, among titles sharing most of them",
            ),
            pytest.param(
                "WG Revenue Claims Final Payment Checklist - Supervisory Checks",
                CHECKLIST,
                id="by the start of a long title",
            ),
            pytest.param(f"{BREACHES} 2024", BREACHES, id="by more words than it has"),
        ],
    )
    async def test_puts_the_guide_meant_first(self, finding, words, title):
        kept = await finding.stored(TITLES)

        found = await finding.search.like(words, limit=8)

        assert (found[0].title, found[0].guide_id) == (title, kept[title])

    async def test_gives_no_more_than_asked_for(self, finding):
        await finding.stored(TITLES)

        found = await finding.search.like("claim", limit=2)

        assert len(found) == 2

    async def test_finds_nothing_for_no_words(self, finding):
        await finding.stored(TITLES)

        assert await finding.search.like(" – ", limit=8) == ()

    async def test_finds_no_title_unlike_every_one(self, finding):
        await finding.stored(TITLES)

        assert await finding.search.like("xyzzy", limit=8) == ()
