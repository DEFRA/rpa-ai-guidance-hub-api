"""Links in a guide that name a guide in the hub, pointed at that guide's page.

A link names a guide when its canonical link is the source link of one of the
guide's versions (`records.guides_known_by`). Only its address changes, to the hub's
page for the guide, which shows its latest version; its words and the rest of the
guide stay as written.

The address is changed where it is written as the Word converter writes it
(`link_destinations.written`: bare, or in angle brackets where bare would be
misread). The
same text anywhere else, such as in code, would change too; the converter writes
none.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from app.guidance import link_targets, reading
from app.guidance.parsing import link_destinations

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.guidance import ids


@dataclasses.dataclass(frozen=True)
class DocumentLink:
    """A link in a guide that names a document: where it goes, as written, and its
    canonical link."""

    destination: str
    canonical_link: str


def document_links(markdown: str) -> list[DocumentLink]:
    """The guide's links that name a document, in order."""
    found: list[DocumentLink] = []
    for link in reading.links_of(markdown):
        target = link_targets.target_of(link.destination)
        if target.kind in link_targets.NAMING_A_DOCUMENT:
            found.append(DocumentLink(link.destination, str(target.canonical_link)))
    return found


def pointed_at(
    markdown: str,
    links: Sequence[DocumentLink],
    guides: Mapping[str, ids.DocumentId],
) -> str:
    """`markdown`, with each of its `links` whose canonical link `guides` holds
    pointed at the page of the guide it names."""
    pages = {
        link.destination: f"/guides/{guides[link.canonical_link]}"
        for link in links
        if link.canonical_link in guides
    }
    for destination, page in pages.items():
        markdown = markdown.replace(
            f"]({link_destinations.written(destination)})",
            f"]({link_destinations.written(page)})",
        )
    return markdown
