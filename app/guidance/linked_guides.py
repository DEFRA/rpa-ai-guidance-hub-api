"""Links in a guide that name a guide in the hub, pointed at that guide's page.

A link names a guide when its canonical link is the source link of one of the
guide's versions (`records.guides_known_by`). Only its address changes, to the hub's
page for the guide, which shows its latest version; its words, its title and the rest
of the guide stay as written. The address is changed only in the lines of the block
the parser finds the link in, so the same text in a code block is left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.guidance import link_targets, reading

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.guidance import ids


def canonical_links_in(markdown: str) -> set[str]:
    """The canonical link of each link in the guide that names a document."""
    found = {_canonical(link.destination) for link in reading.link_lines(markdown)}
    return {canonical for canonical in found if canonical is not None}


def pointed_at(markdown: str, guides: Mapping[str, ids.DocumentId]) -> str:
    """`markdown`, with each link whose canonical link `guides` holds pointed at the
    page of the guide it names."""
    # Split as the parser counts lines, so that a link's lines are these.
    lines = markdown.split("\n")
    for link in reading.link_lines(markdown):
        canonical = _canonical(link.destination)
        guide = None if canonical is None else guides.get(canonical)
        if guide is None:
            continue
        for at in range(link.first, link.end):
            lines[at] = _readdressed(lines[at], link.destination, f"/guides/{guide}")
    return "\n".join(lines)


def _canonical(destination: str) -> str | None:
    """The canonical link of a link that names a document, or None."""
    target = link_targets.target_of(destination)
    if target.kind not in link_targets.NAMING_A_DOCUMENT:
        return None
    return str(target.canonical_link)


def _readdressed(line: str, destination: str, page: str) -> str:
    """`line`, with each link to `destination` going to `page`: written in angle
    brackets, on its own, or before a title."""
    for before, after in (
        (f"](<{destination}>", f"](<{page}>"),
        (f"]({destination})", f"]({page})"),
        (f"]({destination} ", f"]({page} "),
    ):
        line = line.replace(before, after)
    return line
