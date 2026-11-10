"""The shapes a user takes over HTTP. Pydantic, validated on the way in, written into
the OpenAPI document, and spelt in camelCase on the wire; they convert to and from
`models`, and know nothing of how anything is stored.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated, Literal

import pydantic

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import schemas as guidance_schemas
from app.users import ids, models

if TYPE_CHECKING:
    from collections.abc import Mapping


class Identity(pydantic.BaseModel):
    """Who signed in, as the identity provider names them: the tenant, and the
    user's object id within it. Unique together."""

    tenant_id: str = pydantic.Field(alias="tenantId", min_length=1)
    object_id: str = pydantic.Field(alias="objectId", min_length=1)

    model_config = pydantic.ConfigDict(populate_by_name=True)


class SignedIn(Identity):
    """Someone who has just signed in, as the UI knows them from their token."""

    display_name: str | None = pydantic.Field(default=None, alias="displayName")


class OpenedGuide(pydantic.BaseModel):
    """A guide the user has just opened."""

    guide_id: guidance_ids.DocumentId = pydantic.Field(alias="guideId")


class PinInBrief(pydantic.BaseModel):
    """A guide the user has pinned, or a section of one, as the side navigation shows
    it: the guide's id and title, and for a section, its id, its anchor and its
    heading as it read when it was pinned."""

    id: guidance_ids.DocumentId
    title: str | None
    section_id: guidance_ids.SectionId | None = pydantic.Field(alias="sectionId")
    section: str | None
    heading: str | None

    @classmethod
    def from_model(cls, pin: models.Pin, guide: models.GuideInBrief) -> PinInBrief:
        return cls(
            id=pin.document_id,
            title=guide.title,
            sectionId=pin.section_id,
            section=None
            if pin.section_id is None
            else guide.sections.anchor_of(pin.section_id),
            heading=pin.heading,
        )


class PlacedBookmark(pydantic.BaseModel):
    """A section bookmarked to a case, as the side navigation shows it: its id, its
    anchor and heading, and where the bookmark is among the case's, from 0, which is
    what a patch that moves or removes it names it by."""

    section_id: guidance_ids.SectionId = pydantic.Field(alias="sectionId")
    section: str | None
    heading: str | None
    position: int


class CaseGuideInBrief(pydantic.BaseModel):
    """A guide bookmarked to a case, as the side navigation names it, with its
    bookmarked sections in the order the guide has them."""

    id: guidance_ids.DocumentId
    title: str | None
    bookmarks: list[PlacedBookmark]


class CaseInBrief(pydantic.BaseModel):
    """A case the user has bookmarked guides to, as the side navigation shows it:
    its key, its type and reference, and its guides in the order first bookmarked."""

    key: ids.CaseKey
    type: models.CaseType
    ref: str
    guides: list[CaseGuideInBrief]

    @classmethod
    def from_model(
        cls,
        case: models.Case,
        guides: Mapping[guidance_ids.DocumentId, models.GuideInBrief],
    ) -> CaseInBrief:
        """The case, its guides as `guides` has them; a guide not there is one that
        no longer exists, and is left out."""
        by_guide: dict[guidance_ids.DocumentId, list[tuple[int, models.Bookmark]]] = {}
        for position, bookmark in enumerate(case.bookmarks):
            by_guide.setdefault(bookmark.document_id, []).append((position, bookmark))
        return cls(
            key=case.key,
            type=case.type,
            ref=case.ref,
            guides=[
                CaseGuideInBrief(
                    id=guide,
                    title=guides[guide].title,
                    bookmarks=_in_guide_order(bookmarks, guides[guide].sections),
                )
                for guide, bookmarks in by_guide.items()
                if guide in guides
            ],
        )


def _in_guide_order(
    bookmarks: list[tuple[int, models.Bookmark]], sections: models.GuideSections
) -> list[PlacedBookmark]:
    """A guide's bookmarks in the order the guide has their sections; one whose
    section the guide does not have now, last."""
    return [
        PlacedBookmark(
            sectionId=bookmark.section_id,
            section=sections.anchor_of(bookmark.section_id),
            heading=bookmark.heading,
            position=position,
        )
        for position, bookmark in sorted(
            bookmarks, key=lambda each: (sections.order_of(each[1].section_id), each[0])
        )
    ]


class User(pydantic.BaseModel):
    """A user as answered: the hub's own id for them, who they are to the identity
    provider, their name as of their latest sign-in, and when they first signed
    in. With them, what the side navigation shows of their own lists, so a page asks
    once: the guides and sections they have pinned, in their order, and their cases."""

    id: ids.UserId
    identity: Identity
    display_name: str | None = pydantic.Field(alias="displayName")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")
    pinned: list[PinInBrief]
    cases: list[CaseInBrief]

    @classmethod
    def from_model(
        cls,
        user: models.User,
        guides: Mapping[guidance_ids.DocumentId, models.GuideInBrief],
    ) -> User:
        """The user, with their pins and their cases, each with the guides it names
        as `guides` has them; a guide not there is one that no longer exists, and is
        left out."""
        return cls(
            id=user.id,
            identity=Identity(tenantId=user.tenant_id, objectId=user.object_id),
            displayName=user.display_name,
            createdAt=user.created_at,
            pinned=[
                PinInBrief.from_model(pin, guides[pin.document_id])
                for pin in user.pins
                if pin.document_id in guides
            ],
            cases=[CaseInBrief.from_model(case, guides) for case in user.cases],
        )


class PinnedSummary(guidance_schemas.DocumentSummary):
    """A pin as the Pinned page lists it: its guide as `GET /guides` lists it, with
    the pin's position among all the user's pins, which a patch of the pins takes,
    whatever the filters show; and for a section, its id, its anchor in the guide's
    latest version and its heading as it read when it was pinned."""

    position: int
    section_id: guidance_ids.SectionId | None = pydantic.Field(alias="sectionId")
    section: str | None
    heading: str | None

    @classmethod
    def from_listed(cls, listed: models.ListedPin) -> PinnedSummary:
        summary = guidance_schemas.DocumentSummary.from_model(listed.summary)
        return cls(
            **summary.model_dump(by_alias=True),
            position=listed.position,
            sectionId=listed.pin.section_id,
            section=listed.anchor,
            heading=listed.pin.heading,
        )


class Pinned(pydantic.BaseModel):
    """The user's pins, guides and sections of guides, in their order, each with its
    guide as `GET /guides` lists it, and each filter's values among their guides
    with their counts."""

    items: list[PinnedSummary]
    counts: dict[str, list[guidance_schemas.OptionCount]]


class RecentlyOpenedSummary(guidance_schemas.DocumentSummary):
    """A guide the user opened recently, as `GET /guides` lists it, with when they
    last opened it."""

    opened_at: dt.datetime = pydantic.Field(alias="openedAt")

    @classmethod
    def opened(
        cls, summary: guidance_models.DocumentSummary, opened_at: dt.datetime
    ) -> RecentlyOpenedSummary:
        listed = guidance_schemas.DocumentSummary.from_model(summary)
        return cls(**listed.model_dump(by_alias=True), openedAt=opened_at)


class RecentlyOpened(pydantic.BaseModel):
    """The guides the user opened most recently, newest first, as `GET /guides`
    lists them, and each filter's values among them with their counts."""

    items: list[RecentlyOpenedSummary]
    counts: dict[str, list[guidance_schemas.OptionCount]]


class BookmarkedSection(pydantic.BaseModel):
    """A section bookmarked to a case, as the Bookmarked guides page shows it: its
    id; its anchor in the guide's latest version, none for a section the guide no
    longer has; its heading as it read when it was bookmarked; and the first
    paragraph under it now, as plain text, none for a section with no words of its
    own."""

    section_id: guidance_ids.SectionId = pydantic.Field(alias="sectionId")
    section: str | None
    heading: str | None
    first_paragraph: str | None = pydantic.Field(alias="firstParagraph")

    @classmethod
    def from_model(cls, section: models.BookmarkedSection) -> BookmarkedSection:
        return cls(
            sectionId=section.section_id,
            section=section.anchor,
            heading=section.heading,
            firstParagraph=section.first_paragraph,
        )


class BookmarkedGuide(guidance_schemas.DocumentSummary):
    """A guide bookmarked to a case, as `GET /guides` lists it, with its sections
    bookmarked to the case in the order the guide has them."""

    sections: list[BookmarkedSection]

    @classmethod
    def bookmarked(cls, guide: models.BookmarkedGuide) -> BookmarkedGuide:
        listed = guidance_schemas.DocumentSummary.from_model(guide.summary)
        return cls(
            **listed.model_dump(by_alias=True),
            sections=[BookmarkedSection.from_model(s) for s in guide.sections],
        )


class BookmarkedCase(pydantic.BaseModel):
    """A case, by its key, its type and its reference, with the guides bookmarked to
    it in the user's order."""

    key: ids.CaseKey
    type: models.CaseType
    ref: str
    guides: list[BookmarkedGuide]

    @classmethod
    def from_model(cls, listed: models.BookmarkedCase) -> BookmarkedCase:
        return cls(
            key=listed.case.key,
            type=listed.case.type,
            ref=listed.case.ref,
            guides=[BookmarkedGuide.bookmarked(guide) for guide in listed.guides],
        )


class BookmarkedGuides(pydantic.BaseModel):
    """The user's cases, each with the guides bookmarked to it, and each filter's
    values among those guides, each guide counted once."""

    cases: list[BookmarkedCase]
    counts: dict[str, list[guidance_schemas.OptionCount]]


class _PatchOperation(pydantic.BaseModel):
    """A JSON Patch operation (RFC 6902) on what its JSON Pointer `path` names."""

    path: str

    model_config = pydantic.ConfigDict(extra="forbid")


class AddOperation(_PatchOperation):
    """Put `value` at `path`: into a list at a position (`-` is the end), or as a
    member of an object."""

    op: Literal["add"]
    value: pydantic.JsonValue


class RemoveOperation(_PatchOperation):
    """Take out what `path` names."""

    op: Literal["remove"]


class MoveOperation(_PatchOperation):
    """Take out what `from` names, then add it at `path`, which is read in the
    document without it."""

    op: Literal["move"]
    from_: str = pydantic.Field(alias="from")


class TestOperation(_PatchOperation):
    """Check that `path` still holds `value`, so a patch made on what has changed
    since applies nothing."""

    op: Literal["test"]
    value: pydantic.JsonValue


PatchOperation = Annotated[
    AddOperation | RemoveOperation | MoveOperation | TestOperation,
    pydantic.Field(discriminator="op"),
]


class UnknownSectionError(ValueError):
    """Raised for a pin or a bookmark of a section the guide does not have."""


def _section_named(
    guide_id: guidance_ids.DocumentId,
    section_id: guidance_ids.SectionId | None,
    anchor: str | None,
    sections: models.GuideSections | None,
) -> guidance_ids.SectionId | None:
    """The section a pin or a bookmark names: by its id, as it is; else by its
    anchor, the section the guide's latest version has there (`sections`). None if
    it names neither.

    Raises:
        UnknownSectionError: for an anchor the guide does not have.
    """
    if section_id is not None or anchor is None:
        return section_id
    found = None if sections is None else sections.id_at(anchor)
    if found is None:
        message = f"Guide {guide_id} has no section {anchor!r}"
        raise UnknownSectionError(message)
    return found


class Pin(pydantic.BaseModel):
    """A guide the user has pinned, or a section of one, as the user's pins are
    patched whole: a whole guide has no section. A section is kept by its id, and
    answered with its anchor too (`section`); one added by its anchor alone is the
    section the guide has there. `heading` is the section's heading as it read when
    it was pinned."""

    guide_id: guidance_ids.DocumentId = pydantic.Field(alias="guideId")
    section_id: guidance_ids.SectionId | None = pydantic.Field(
        default=None, alias="sectionId"
    )
    section: str | None = pydantic.Field(default=None, min_length=1)
    heading: str | None = None

    model_config = pydantic.ConfigDict(extra="forbid", populate_by_name=True)

    @pydantic.model_validator(mode="after")
    def _heading_of_a_section(self) -> Pin:
        if (
            self.heading is not None
            and self.section_id is None
            and self.section is None
        ):
            message = "Only a pinned section has a heading"
            raise ValueError(message)
        return self

    @classmethod
    def from_model(cls, pin: models.Pin, sections: models.GuideSections | None) -> Pin:
        return cls(
            guideId=pin.document_id,
            sectionId=pin.section_id,
            section=None
            if pin.section_id is None or sections is None
            else sections.anchor_of(pin.section_id),
            heading=pin.heading,
        )

    def to_model(self, sections: models.GuideSections | None) -> models.Pin:
        """Raises:
        UnknownSectionError: as `_section_named`.
        """
        return models.Pin(
            document_id=self.guide_id,
            section_id=_section_named(
                self.guide_id, self.section_id, self.section, sections
            ),
            heading=self.heading,
        )


class CaseBookmark(pydantic.BaseModel):
    """A section of a guide bookmarked to a case, as the user's cases are patched
    whole. It is kept by the section's id, and answered with the section's anchor
    too (`section`); one added by its anchor alone is the section the guide has
    there. `heading` is the section's heading as it read when it was bookmarked;
    `createdAt` is set when the bookmark is added, if it is not given."""

    guide_id: guidance_ids.DocumentId = pydantic.Field(alias="guideId")
    section_id: guidance_ids.SectionId | None = pydantic.Field(
        default=None, alias="sectionId"
    )
    section: str | None = pydantic.Field(default=None, min_length=1)
    heading: str | None = None
    created_at: dt.datetime | None = pydantic.Field(default=None, alias="createdAt")

    model_config = pydantic.ConfigDict(extra="forbid", populate_by_name=True)

    @classmethod
    def from_model(
        cls, bookmark: models.Bookmark, sections: models.GuideSections | None
    ) -> CaseBookmark:
        return cls(
            guideId=bookmark.document_id,
            sectionId=bookmark.section_id,
            section=None
            if sections is None
            else sections.anchor_of(bookmark.section_id),
            heading=bookmark.heading,
            createdAt=bookmark.created_at,
        )

    def to_model(
        self, now: dt.datetime, sections: models.GuideSections | None
    ) -> models.Bookmark:
        """Raises:
        UnknownSectionError: if the bookmark names no section, or names one by an
            anchor the guide does not have.
        """
        section_id = _section_named(
            self.guide_id, self.section_id, self.section, sections
        )
        if section_id is None:
            message = f"A bookmark of guide {self.guide_id} names no section"
            raise UnknownSectionError(message)
        return models.Bookmark(
            document_id=self.guide_id,
            section_id=section_id,
            heading=self.heading,
            created_at=self.created_at or now,
        )


class Case(pydantic.BaseModel):
    """A case the user has bookmarked guides to, as their cases are patched whole:
    its key (given to a new case), its type and reference, and its bookmarks in the
    user's order. A Case ID is a letter, kept upper case, then exactly seven digits;
    an SBI is nine digits, from 105000000 to 999999999."""

    key: ids.CaseKey | None = None
    type: models.CaseType
    ref: str
    bookmarks: list[CaseBookmark]

    model_config = pydantic.ConfigDict(extra="forbid")

    @pydantic.model_validator(mode="after")
    def _ref_as_kept(self) -> Case:
        self.ref = models.kept_reference(self.type, self.ref)
        return self

    @classmethod
    def from_model(
        cls,
        case: models.Case,
        sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
    ) -> Case:
        """The case, each bookmarked section at its anchor (`sections`, by guide)."""
        return cls(
            key=case.key,
            type=case.type,
            ref=case.ref,
            bookmarks=[
                CaseBookmark.from_model(b, sections.get(b.document_id))
                for b in case.bookmarks
            ],
        )

    def to_model(
        self,
        now: dt.datetime,
        sections: Mapping[guidance_ids.DocumentId, models.GuideSections],
    ) -> models.Case:
        """Raises:
        UnknownSectionError: as `CaseBookmark.to_model`.
        """
        return models.Case(
            key=self.key or ids.new_case_key(),
            type=self.type,
            ref=self.ref,
            bookmarks=tuple(
                bookmark.to_model(now, sections.get(bookmark.guide_id))
                for bookmark in self.bookmarks
            ),
        )
