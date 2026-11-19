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

# The guides of an invented scheme, the Otter Habitat Fund (OHF).
BREACHES = "OHF Riverbank and Pond Breaches and Reductions"
SURVEY_RULE = "OHF Claim – Riverbank Survey Rule at Approval"
MAPPER = "2021 Pond Mapper Basic Navigation Guide"
CASE_REVIEW = "Final Grant Case Review Guide"
CHECKLIST = (
    "OHF Revenue Claims Final Grant Checklist – Supervisory Checks Fill in this "
    "checklist on screen. — Use the note box at the end to explain a check's result."
)
TITLES = (
    SURVEY_RULE,
    "OHF Claim – Missing Survey Photo at Approval",
    "OHF Claim - Claim Refresh Approval Check",
    "OHF Revenue Claims Processing to Final Grant Guide",
    CASE_REVIEW,
    BREACHES,
    MAPPER,
    "Helpline Desk Basic Navigation and Admin",
    "Final Grant Awaiting Approval QC Form",
    "OHF Revenue Claims Supervisory Checks Guidance",
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
                "approval survey rule riverbank",
                SURVEY_RULE,
                id="with its words in another order",
            ),
            pytest.param("pond mapper navigation", MAPPER, id="by some of its words"),
            pytest.param(
                "final grant case",
                CASE_REVIEW,
                id="by the words that start it, among titles sharing most of them",
            ),
            pytest.param(
                "OHF Revenue Claims Final Grant Checklist - Supervisory Checks",
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
