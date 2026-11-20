"""Search in one guide, done in the hub: a stand-in, to be switched for the search
service when it does the same.

It reads the guide's latest version, and finds the words in each section's text as a
reader sees it (`reading`), by the hub's keyword rule (`matching`). A guide is read
whole each time: it holds no index.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from app.guidance import reading
from app.search import guide_search, matching

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


def _matches(text: str, term: str) -> tuple[guide_search.Match, ...]:
    return tuple(
        guide_search.Match(start, end, text[start:end])
        for start, end in matching.matches_in(text, term)
    )
