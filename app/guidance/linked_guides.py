"""Links in a guide that name a guide in the hub, pointed at that guide's page.

A link names a guide when its canonical link is the source link of one of the
guide's versions (`records.guides_known_by`). Only its address changes, to the hub's
page for the guide, which shows its latest version; its words, its title and the rest
of the guide stay as written. The address is changed only in the lines of the block
the parser finds the link in, and only where the parser then finds one more link to
the page, so text that only looks like a link (in code, or in escaped brackets) is
left alone.
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
    """A link in a guide that names a document: where it is, and its canonical link."""

    lines: reading.LinkLines
    canonical_link: str


def document_links(markdown: str) -> list[DocumentLink]:
    """The guide's links that name a document, in order."""
    found: list[DocumentLink] = []
    for link in reading.link_lines(markdown):
        target = link_targets.target_of(link.destination)
        if target.kind in link_targets.NAMING_A_DOCUMENT:
            found.append(DocumentLink(link, str(target.canonical_link)))
    return found


def pointed_at(
    markdown: str,
    links: Sequence[DocumentLink],
    guides: Mapping[str, ids.DocumentId],
) -> str:
    """`markdown`, with each of its `links` whose canonical link `guides` holds
    pointed at the page of the guide it names."""
    if not guides:
        return markdown
    lines = _lines(markdown)
    for link in links:
        guide = guides.get(link.canonical_link)
        if guide is None:
            continue
        first, end = link.lines.first, link.lines.end
        block = "".join(lines[first:end])
        lines[first:end] = [
            _readdressed(block, link.lines.destination, f"/guides/{guide}"),
            *[""] * (end - first - 1),
        ]
    return "".join(lines)


def _lines(text: str) -> list[str]:
    """`text` in lines as the parser counts them, each with its ending: a line ends at
    `\r\n`, `\r` or `\n`. The text after the last ending is a line too."""
    lines: list[str] = []
    start = at = 0
    while at < len(text):
        if text[at] not in "\r\n":
            at += 1
            continue
        at += 2 if text.startswith("\r\n", at) else 1
        lines.append(text[start:at])
        start = at
    lines.append(text[start:])
    return lines


def _readdressed(block: str, destination: str, page: str) -> str:
    """`block`, with each link to `destination` going to `page`: written in angle
    brackets, on its own, or before a title. Each place is changed on its own, and
    kept only when the parser then finds one more link to `page`."""
    for before, after in (
        (f"](<{destination}>", f"](<{page}>"),
        (f"]({destination})", f"]({page})"),
        (f"]({destination} ", f"]({page} "),
    ):
        start = block.find(before)
        while start >= 0:
            changed = block[:start] + after + block[start + len(before) :]
            if _links_to(changed, page) > _links_to(block, page):
                block, start = changed, start + len(after)
            else:
                start += len(before)
            start = block.find(before, start)
    return block


def _links_to(block: str, page: str) -> int:
    """How many links in `block` the parser finds going to `page`. Read without each
    line's indent, which out of its list would make the block code."""
    text = "".join(line.lstrip(" \t") for line in _lines(block))
    return sum(link.destination == page for link in reading.link_lines(text))
