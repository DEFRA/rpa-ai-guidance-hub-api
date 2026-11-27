"""Links in a guide that name a guide in the hub, pointed at that guide's page.

A link names a guide when its canonical link is the source link of one of the
guide's versions (`records.guides_known_by`). Only its address changes, to the hub's
page for the guide, which shows its latest version; its words, its title and the rest
of the guide stay as written. The parser says where each address is written
(`reading.link_addresses`), so text that only looks like a link, in code or in
escaped brackets, is never changed.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from app.guidance import link_targets, reading

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.guidance import ids


@dataclasses.dataclass(frozen=True)
class DocumentLink:
    """A link in a guide that names a document: where its address is, and its
    canonical link."""

    address: reading.LinkAddress
    canonical_link: str


def document_links(markdown: str) -> list[DocumentLink]:
    """The guide's links that name a document, in order."""
    found: list[DocumentLink] = []
    for address in reading.link_addresses(markdown):
        target = link_targets.target_of(address.destination)
        if target.kind in link_targets.NAMING_A_DOCUMENT:
            found.append(DocumentLink(address, str(target.canonical_link)))
    return found


def pointed_at(
    markdown: str,
    links: Sequence[DocumentLink],
    guides: Mapping[str, ids.DocumentId],
) -> str:
    """`markdown`, with each of its `links` whose canonical link `guides` holds
    pointed at the page of the guide it names. An address in angle brackets keeps
    them."""
    matched = [
        (link.address, f"/guides/{guides[link.canonical_link]}")
        for link in links
        if link.canonical_link in guides
    ]
    for address, page in sorted(matched, key=lambda each: each[0].start, reverse=True):
        written = markdown[address.start : address.end]
        new = f"<{page}>" if written.startswith("<") else page
        markdown = markdown[: address.start] + new + markdown[address.end :]
    return markdown
