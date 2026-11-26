"""The anchor a heading renders with, and so the target a cross-reference points at.

A cross-reference has to name something the hub's pages can find. Word names a
bookmark, which means nothing outside the .docx, so the anchor is the slug of the
heading as it is rendered, and this module is the one place in the API that says what
that slug is. `models` calls it to write a link and the reader calls it to recognise
one, and a document round-trips only while both get the same answer. The UI and the
search service make the same slug (`src/services/guide-sections.js`,
`app/indexing/prepare/anchors.py`).

The rule is GitHub's, but for one thing: a dot between two digits becomes a hyphen, so
`### 3.1 Overview` is `#3-1-overview`, and "1.2" is not read as "12". Anchors made
before that dropped the dot (`#31-overview`), and guides converted then still carry
them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.guidance.parsing import models

# Everything a slug drops. Word characters, spaces and hyphens survive; the rest - a
# colon, a comma, a dot not between two digits - is removed rather than replaced.
_DISCARDED = re.compile(r"[^\w\- ]", re.UNICODE)

_DIGITS = frozenset("0123456789")


def slugify(text: str) -> str:
    """The anchor the hub gives a heading printing `text`."""
    return _DISCARDED.sub("", _numbers_hyphenated(text).lower()).replace(" ", "-")


def _numbers_hyphenated(text: str) -> str:
    """`text` with each dot between two digits made a hyphen: "3.1" is "3-1"."""
    return "".join(
        "-" if char == "." and _between_digits(text, at) else char
        for at, char in enumerate(text)
    )


def _between_digits(text: str, at: int) -> bool:
    return 0 < at < len(text) - 1 and {text[at - 1], text[at + 1]} <= _DIGITS


def of_document(sections: list[models.MarkdownSection]) -> dict[int, str]:
    """Every section's anchor, keyed by the identity of the section.

    Keyed by `id()` because a section is not hashable - `MarkdownSection` compares by
    value and holds a `parent`, so it cannot be a dict key - and because identity is
    what is wanted anyway: two sections that compare equal still render two headings
    and need two anchors.

    A duplicate is suffixed `-1`, `-2` in document order, as a renderer does. Numbered
    headings cannot collide, their number being part of what they print; only
    top-level appendices can, printing their heading alone.
    """
    anchors: dict[int, str] = {}
    seen: dict[str, int] = {}

    for section in sections:
        base = slugify(section.rendered_heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors[id(section)] = base if count == 0 else f"{base}-{count}"

    return anchors
