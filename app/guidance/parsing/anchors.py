"""The anchor a heading renders with, and so the target a cross-reference points at.

A cross-reference has to name something a renderer can find. Word names a bookmark,
which means nothing outside the .docx; the obvious replacement - the section's own
number - means nothing to a renderer either, because GFM builds a heading's id from
the words it prints. `### 3.1 Overview` is reachable at `#31-overview` and at nothing
else, so `](#3.1)` is a link to a place that does not exist.

So the anchor is the slug of the heading as it is rendered, and this module is the one
place that says what that slug is. `models` calls it to write a link and the reader
calls it to recognise one, and a document round-trips only while both get the same
answer - which is the whole reason the rule is not written down twice.

Slugification is not standardised: GitHub, GitLab, pandoc and python-markdown differ
on punctuation, on case and on non-ASCII. The rule below is GitHub's, because it is
the one a reader is most likely to be holding the document up against. A document
stored under it resolves in renderers that share it and dangles in those that do not,
and that is a property of the format rather than of this code.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.guidance.parsing import models

# Everything a slug drops. Word characters, spaces and hyphens survive; the rest -
# the "." in "3.1", a colon, a comma - is removed rather than replaced, which is what
# turns "3.1 Overview" into "31-overview" and not "3-1-overview".
_DISCARDED = re.compile(r"[^\w\- ]", re.UNICODE)


def slugify(text: str) -> str:
    """The anchor a renderer builds for a heading printing `text`."""
    return _DISCARDED.sub("", text.lower()).replace(" ", "-")


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
