"""A user as the service holds them, converting itself to and from the record Mongo
stores, so the names a record is stored under are written down once, here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.users import ids

# Who signed in, as the identity provider names them: the tenant and the user's
# object id within it. Nested only as stored.
IDENTITY = "identity"
TENANT_ID = f"{IDENTITY}.tenantId"
OBJECT_ID = f"{IDENTITY}.objectId"

# The guides, and sections of guides, the user has pinned, in their own order.
PINS = "pins"

# The user's latest openings of guides, in the order they reached the database.
RECENTLY_OPENED = "recentlyOpened"

# How many guides a user's recently opened list keeps.
RECENTLY_OPENED_KEPT = 5

# The cases the user has bookmarked guides to, in their own order, each with its
# bookmarks in order.
CASES = "cases"

# What a case is known by: a Case ID, or an SBI (Single Business Identifier).
CaseType = Literal["case", "sbi"]

# A Case ID is a letter, then this many digits.
_CASE_ID_DIGITS = 7

# An SBI is nine digits, and only those in this range are SBIs: DEFRA's services
# accept no others, and CRM and FDM validate against the same range.
_SBI_DIGITS = 9
_SBIS = range(105_000_000, 1_000_000_000)


def kept_reference(case_type: CaseType, ref: str) -> str:
    """A case's reference as it is kept: a Case ID's letter in upper case; an SBI as
    it is.

    Raises:
        ValueError: if `ref` is not a reference of that type.
    """
    if case_type == "case" and _is_case_id(ref):
        return ref.upper()
    if case_type == "sbi" and _is_sbi(ref):
        return ref
    message = f"{ref!r} is not a reference of type {case_type!r}"
    raise ValueError(message)


def _is_case_id(ref: str) -> bool:
    letter, digits = ref[:1], ref[1:]
    return (
        letter.isascii()
        and letter.isalpha()
        and _are_digits(digits)
        and len(digits) == _CASE_ID_DIGITS
    )


def _is_sbi(ref: str) -> bool:
    return _are_digits(ref) and len(ref) == _SBI_DIGITS and int(ref) in _SBIS


def _are_digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


@dataclasses.dataclass(frozen=True)
class Opened:
    """A guide the user opened, and when they last did."""

    document_id: guidance_ids.DocumentId
    opened_at: dt.datetime

    def to_document(self) -> dict[str, Any]:
        return {"documentId": self.document_id, "openedAt": self.opened_at}

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Opened:
        return cls(document_id=document["documentId"], opened_at=document["openedAt"])


@dataclasses.dataclass(frozen=True)
class Pin:
    """A guide the user has pinned, or a section of one, by the section's id, so that
    it keeps its section when a new version renumbers it; with the section's heading
    as it read when it was pinned. A whole guide has neither."""

    document_id: guidance_ids.DocumentId
    section_id: guidance_ids.SectionId | None = None
    heading: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "sectionId": self.section_id,
            "heading": self.heading,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Pin:
        return cls(
            document_id=document["documentId"],
            section_id=document.get("sectionId"),
            heading=document.get("heading"),
        )


@dataclasses.dataclass(frozen=True)
class Bookmark:
    """A section of a guide bookmarked to a case, by the section's id, so that it
    keeps its section when a new version renumbers it; with the section's heading as
    it read when it was bookmarked."""

    document_id: guidance_ids.DocumentId
    section_id: guidance_ids.SectionId
    heading: str | None
    created_at: dt.datetime

    def to_document(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "sectionId": self.section_id,
            "heading": self.heading,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Bookmark:
        return cls(
            document_id=document["documentId"],
            section_id=document["sectionId"],
            heading=document.get("heading"),
            created_at=document["createdAt"],
        )


@dataclasses.dataclass(frozen=True)
class Case:
    """A case the user has bookmarked guides to: its key, which names it wherever
    its reference must not go, its type and reference (`kept_reference`), and its
    bookmarks in the user's order."""

    key: ids.CaseKey
    type: CaseType
    ref: str
    bookmarks: tuple[Bookmark, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "ref": self.ref,
            "bookmarks": [bookmark.to_document() for bookmark in self.bookmarks],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Case:
        return cls(
            key=document["key"],
            type=document["type"],
            ref=document["ref"],
            bookmarks=tuple(
                Bookmark.from_document(bookmark)
                for bookmark in document.get("bookmarks", [])
            ),
        )


@dataclasses.dataclass(frozen=True)
class GuideSections:
    """A guide's sections as its latest version has them: each anchor with the id it
    was given, in the guide's order."""

    by_anchor: Mapping[str, guidance_ids.SectionId]

    def id_at(self, anchor: str) -> guidance_ids.SectionId | None:
        return self.by_anchor.get(anchor)

    def has(self, section_id: guidance_ids.SectionId) -> bool:
        return section_id in self.by_anchor.values()

    def anchor_of(self, section_id: guidance_ids.SectionId) -> str | None:
        return next((a for a, s in self.by_anchor.items() if s == section_id), None)

    def order_of(self, section_id: guidance_ids.SectionId) -> int:
        """Where the section comes in the guide; one it does not have, after all of
        them."""
        in_order = list(self.by_anchor.values())
        if section_id in in_order:
            return in_order.index(section_id)
        return len(in_order)


@dataclasses.dataclass(frozen=True)
class ListedPin:
    """A pin as the Pinned page lists it: its guide as `GET /guides` lists it, the
    pin, a section's anchor in the guide's latest version (none for a whole guide,
    or a section it no longer has), and where the pin is among all the user's
    pins."""

    summary: guidance_models.DocumentSummary
    pin: Pin
    anchor: str | None
    position: int


@dataclasses.dataclass(frozen=True)
class BookmarkedSection:
    """A section bookmarked to a case, as the Bookmarked guides page shows it: its
    id; its anchor in the guide's latest version, none for a section the guide no
    longer has; its heading as it read when it was bookmarked; and the first
    paragraph under it now, none for a section with no words of its own."""

    section_id: guidance_ids.SectionId
    anchor: str | None
    heading: str | None
    first_paragraph: str | None


@dataclasses.dataclass(frozen=True)
class BookmarkedGuide:
    """A guide bookmarked to a case, as `GET /guides` lists it, with its sections
    bookmarked to the case in the order the guide has them."""

    summary: guidance_models.DocumentSummary
    sections: tuple[BookmarkedSection, ...]


@dataclasses.dataclass(frozen=True)
class BookmarkedCase:
    """A case, with the guides bookmarked to it in the user's order."""

    case: Case
    guides: tuple[BookmarkedGuide, ...]


@dataclasses.dataclass(frozen=True)
class GuideInBrief:
    """A guide the user's pins or cases name, as the side navigation names it: its
    title, and its sections."""

    title: str | None
    sections: GuideSections


def _recently_opened(openings: Iterable[Opened]) -> tuple[Opened, ...]:
    """The newest `RECENTLY_OPENED_KEPT` guides, each by its latest opening, newest
    first."""
    latest: dict[guidance_ids.DocumentId, Opened] = {}
    for opening in sorted(openings, key=lambda o: o.opened_at, reverse=True):
        latest.setdefault(opening.document_id, opening)
    return tuple(latest.values())[:RECENTLY_OPENED_KEPT]


@dataclasses.dataclass(frozen=True)
class User:
    """Someone who has signed in to the hub.

    The display name is a copy from their latest sign-in, so a page can name them
    without asking a directory. `pins` and `cases` are in the user's own order;
    `recently_opened` is worked out from the stored openings, newest first.
    """

    id: ids.UserId
    tenant_id: str
    object_id: str
    display_name: str | None
    created_at: dt.datetime
    pins: tuple[Pin, ...] = ()
    recently_opened: tuple[Opened, ...] = ()
    cases: tuple[Case, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            IDENTITY: {"tenantId": self.tenant_id, "objectId": self.object_id},
            "displayName": self.display_name,
            "createdAt": self.created_at,
            PINS: [pin.to_document() for pin in self.pins],
            RECENTLY_OPENED: [opened.to_document() for opened in self.recently_opened],
            CASES: [case.to_document() for case in self.cases],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> User:
        identity = document[IDENTITY]
        return cls(
            id=document["_id"],
            tenant_id=identity["tenantId"],
            object_id=identity["objectId"],
            display_name=document.get("displayName"),
            created_at=document["createdAt"],
            pins=tuple(Pin.from_document(pin) for pin in document.get(PINS, [])),
            recently_opened=_recently_opened(
                Opened.from_document(opened)
                for opened in document.get(RECENTLY_OPENED, [])
            ),
            cases=tuple(Case.from_document(case) for case in document.get(CASES, [])),
        )
