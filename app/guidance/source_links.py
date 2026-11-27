"""The SharePoint link a maintainer gives for the document they upload: where a
version came from.

It is taken only when it names a document for certain (`link_targets`): a named Word
document, or a Document ID link, also behind an Outlook safe link. It is kept as given,
beside its canonical link, by which it can be matched with the links in guides.
"""

from __future__ import annotations

import dataclasses
import enum

from app.guidance import link_targets

_SHAREPOINT = (*link_targets.NAMING_A_DOCUMENT, link_targets.Kind.SHAREPOINT_OTHER)


class Refusal(enum.StrEnum):
    """Why a link is not taken: it is not SharePoint's at all, or it is, but names no
    Word document (a folder, a page, a sharing link that names nothing, another kind of
    file)."""

    NOT_SHAREPOINT = "not-sharepoint"
    NOT_A_WORD_DOCUMENT = "not-a-word-document"


class RefusedSourceLinkError(ValueError):
    """Raised when a link is not taken, with why (`reason`)."""

    def __init__(self, reason: Refusal) -> None:
        super().__init__(f"Not a source link: {reason}")
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class SourceLink:
    link: str
    canonical_link: str


def checked(link: str) -> SourceLink:
    """`link`, kept as given, with its canonical link.

    Raises:
        RefusedSourceLinkError: if it does not name a Word document for certain.
    """
    target = link_targets.target_of(link)
    if target.kind in link_targets.NAMING_A_DOCUMENT:
        return SourceLink(link, str(target.canonical_link))
    raise RefusedSourceLinkError(
        Refusal.NOT_A_WORD_DOCUMENT
        if target.kind in _SHAREPOINT
        else Refusal.NOT_SHAREPOINT
    )
