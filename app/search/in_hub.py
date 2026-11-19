"""Searches done in the hub: stand-ins, to be switched for the search service when it
does the same. They hold no index.

Search in one guide reads the guide's latest version, and finds the words in each
section's text as a reader sees it (`reading`), by the hub's keyword rule (`matching`).

Finding guides by title reads every guide's title, and ranks those like the one given
by the hub's rule for titles (`likeness`).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from app.guidance import reading
from app.search import guide_search, likeness, matching, title_search

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.guidance import ids


@dataclasses.dataclass(frozen=True)
class Guide:
    """What a search in one guide reads: its latest version, and the version's
    Markdown."""

    version_id: ids.VersionId
    markdown: str


class InHubGuideSearch:
    def __init__(
        self, read_guide: Callable[[ids.DocumentId], Awaitable[Guide | None]]
    ) -> None:
        self._read_guide = read_guide

    async def in_guide(
        self, guide_id: ids.DocumentId, words: str
    ) -> guide_search.GuideMatches | None:
        guide = await self._read_guide(guide_id)
        if guide is None:
            return None

        term = matching.term_of(words)
        sections = (
            guide_search.SectionMatches(section.anchor, _matches(section.text, term))
            for section in reading.sections_of(guide.markdown)
        )
        return guide_search.GuideMatches(
            version_id=guide.version_id,
            sections=tuple(section for section in sections if section.matches),
        )


class InHubTitleSearch:
    def __init__(
        self,
        read_titles: Callable[[], Awaitable[Sequence[title_search.GuideTitle]]],
    ) -> None:
        self._read_titles = read_titles

    async def like(self, title: str, limit: int) -> tuple[title_search.GuideTitle, ...]:
        scored = [
            (likeness.score(title, guide.title), guide)
            for guide in await self._read_titles()
        ]
        alike = sorted(
            ((score, guide) for score, guide in scored if score > 0),
            key=lambda pair: (-pair[0], pair[1].title, str(pair[1].guide_id)),
        )
        return tuple(guide for _, guide in alike[:limit])


def _matches(text: str, term: str) -> tuple[guide_search.Match, ...]:
    return tuple(
        guide_search.Match(start, end, text[start:end])
        for start, end in matching.matches_in(text, term)
    )
