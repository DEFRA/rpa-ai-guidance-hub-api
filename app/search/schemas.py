"""Search's shapes over HTTP, in camelCase on the wire."""

from __future__ import annotations

import pydantic

from app.guidance import ids
from app.search import guide_search


class Match(pydantic.BaseModel):
    """Where the words match in a text, from `start` up to `end`, and the words there
    as the text has them."""

    start: int
    end: int
    text: str

    @classmethod
    def from_model(cls, match: guide_search.Match) -> Match:
        return cls(start=match.start, end=match.end, text=match.text)


class SectionMatches(pydantic.BaseModel):
    """A section's matches, in its text as a reader sees it. The section is known by
    its anchor, as pins and bookmarks know it; the introduction has none."""

    anchor: str | None
    matches: list[Match]


class GuideMatches(pydantic.BaseModel):
    """Where the words are in the version searched: in each section holding them, in
    the guide's order."""

    version_id: ids.VersionId = pydantic.Field(alias="versionId")
    sections: list[SectionMatches]

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, found: guide_search.GuideMatches) -> GuideMatches:
        return cls(
            versionId=found.version_id,
            sections=[
                SectionMatches(
                    anchor=section.anchor,
                    matches=[Match.from_model(match) for match in section.matches],
                )
                for section in found.sections
            ],
        )
