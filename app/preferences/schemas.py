"""The shape a user's preferences take over HTTP. Pydantic, validated on the way in,
written into the OpenAPI document, and spelt in camelCase on the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pydantic

if TYPE_CHECKING:
    from collections.abc import Mapping


class Preferences(pydantic.BaseModel):
    """How the user likes the hub shown, the same wherever they sign in: a guide's
    format, its page width, the width of its contents pane and whether its
    information shows under its title; and whether the side navigation is folded,
    its width, and whether each of its lists (Pinned guidance, Case bookmarks) is
    folded. A preference not set is null, and the hub shows its default. They are
    kept by these names.

    Changed by a JSON Merge Patch (RFC 7396) of these alone: a value sets the
    preference, null takes it back to its default."""

    guide_format: Literal["traditional", "stepper"] | None = pydantic.Field(
        default=None, alias="guideFormat"
    )
    reading_width: Literal["standard", "wide", "full"] | None = pydantic.Field(
        default=None, alias="readingWidth"
    )
    contents_width: pydantic.StrictInt | None = pydantic.Field(
        default=None, alias="contentsWidth", ge=240, le=560
    )
    guide_information_shown: pydantic.StrictBool | None = pydantic.Field(
        default=None, alias="guideInformationShown"
    )
    side_nav_collapsed: pydantic.StrictBool | None = pydantic.Field(
        default=None, alias="sideNavCollapsed"
    )
    side_nav_width: pydantic.StrictInt | None = pydantic.Field(
        default=None, alias="sideNavWidth", ge=220, le=440
    )
    side_nav_pinned_collapsed: pydantic.StrictBool | None = pydantic.Field(
        default=None, alias="sideNavPinnedCollapsed"
    )
    side_nav_bookmarks_collapsed: pydantic.StrictBool | None = pydantic.Field(
        default=None, alias="sideNavBookmarksCollapsed"
    )

    model_config = pydantic.ConfigDict(extra="forbid", populate_by_name=True)

    @classmethod
    def from_model(cls, kept: Mapping[str, Any]) -> Preferences:
        """The preferences as they are kept. One the API no longer takes, by its name
        or its value, is not set, so that a change to what it takes never stops a
        user's preferences being read."""
        return cls.model_validate(
            {name: value for name, value in kept.items() if cls._takes(name, value)}
        )

    def changes(self) -> tuple[dict[str, Any], list[str]]:
        """What this patch sets, by name, and the names of those it takes back to
        their defaults."""
        given = self.model_dump(by_alias=True, include=self.model_fields_set)
        chosen = {name: value for name, value in given.items() if value is not None}
        cleared = [name for name, value in given.items() if value is None]
        return chosen, cleared

    @classmethod
    def _takes(cls, name: str, value: object) -> bool:
        try:
            cls.model_validate({name: value})
        except pydantic.ValidationError:
            return False
        return True
