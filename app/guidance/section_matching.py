"""Matches each section of a new version with the same section of the version before,
so that a pin or a bookmark can follow it.

A section is the same when its heading has the same words, its number aside; sections
headed alike match in order. Failing that, it is the same when the words under it are
the same, and only one section left on each side has them. Any other section is new.
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
