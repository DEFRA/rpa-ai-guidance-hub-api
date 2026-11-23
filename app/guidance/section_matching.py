"""Which section of the version before each section of a new version is, so that what
points into a section (a pin, a case bookmark) can follow it to the new version.

Each section of the new version is matched with at most one section before:

1. By the words of its heading, its number aside, so a section renumbered or moved is
   still itself. Sections headed alike are matched in order.
2. Of those left, by the words under its heading, when they are not empty and exactly
   one section left on each side has them: a section whose heading was reworded.
3. Every other section is new.

Words are compared with the spaces between them normalised.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.guidance.parsing import models


def earlier_sections(
    before: Sequence[models.MarkdownSection], after: Sequence[models.MarkdownSection]
) -> list[int | None]:
    """For each section `after`, in order, the index of the same section `before`;
    None for a section that is new."""
    earlier: list[int | None] = [None] * len(after)

    by_heading: dict[str, collections.deque[int]] = collections.defaultdict(
        collections.deque
    )
    for at, section in enumerate(before):
        by_heading[_words(section.heading)].append(at)
    for at, section in enumerate(after):
        alike = by_heading.get(_words(section.heading))
        if alike:
            earlier[at] = alike.popleft()

    matched = set(earlier)
    before_left = _unique_by_content(
        before, [at for at in range(len(before)) if at not in matched]
    )
    after_left = _unique_by_content(
        after, [at for at, found in enumerate(earlier) if found is None]
    )
    for content, at in after_left.items():
        earlier[at] = before_left.get(content)

    return earlier


def _unique_by_content(
    sections: Sequence[models.MarkdownSection], among: Sequence[int]
) -> dict[str, int]:
    """The sections `among`, by the words under their headings, where no other has
    the same words and there are some."""
    said = {at: _words(sections[at].content) for at in among}
    counted = collections.Counter(said.values())
    return {words: at for at, words in said.items() if words and counted[words] == 1}


def _words(text: str) -> str:
    return " ".join(text.split())
