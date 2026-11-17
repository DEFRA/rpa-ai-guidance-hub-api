"""Search in one guide: the interface the rest of the hub calls.

An implementation in the hub, or one that asks the search service, gives it; the
contract tests (`tests/search/contract/`) say what each must do. A match is placed in
its section's text as a reader sees it (`reading.sections_of`), so that the guide's
page can find it among the words it shows.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.guidance import ids


@dataclasses.dataclass(frozen=True)
class Match:
    """Where the words match in a text, from `start` up to `end`, and the words there
    as the text has them, so that a page can check it marks the same words."""

    start: int
    end: int
    text: str


@dataclasses.dataclass(frozen=True)
class SectionMatches:
    """A section's matches, in order. The section is known by its anchor, as pins and
    bookmarks know it; the introduction has none."""

    anchor: str | None
    matches: tuple[Match, ...]


@dataclasses.dataclass(frozen=True)
class GuideMatches:
    """The matches in the version searched: in the guide's title, and in each section
    holding the words, in the guide's order."""

    version_id: ids.VersionId
    title: tuple[Match, ...]
    sections: tuple[SectionMatches, ...]


class GuideSearch(Protocol):
    async def in_guide(
        self, guide_id: ids.DocumentId, words: str
    ) -> GuideMatches | None:
        """Where the words are in the guide's latest version; None for no guide."""
        ...
