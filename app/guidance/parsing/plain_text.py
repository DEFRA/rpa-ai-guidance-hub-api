"""A title or heading line: plain text that Markdown prints as typed.

Word holds a heading as plain text, but a renderer reads its line as Markdown, so
"Use *new* rates" prints in italics without its asterisks. A heading's anchor is built
from what it prints, by the page, by this service and by the search service, each
reading the line its own way, so a heading that Markdown changes gets a different
anchor in each and a link to it goes nowhere.

So each character that can start Markdown in a heading is escaped with a backslash.
Markdown prints the character, and a slug drops the backslash, so every reader builds
the same anchor. Other punctuation, the section number's dots among it, is written as
it is, so a heading with no marks is written as before.

Body text is escaped differently (`inline`), as the editor saves it: `&` as `&amp;`.
In a heading line that would put "amp" in a slug built from the line.
"""

from __future__ import annotations

import re
import string

# Each character that can start Markdown in a heading line: an escape, code, emphasis,
# strikethrough, a link or picture, HTML or an autolink, an entity, and a `#` that
# can close the heading.
_STARTS_MARKDOWN: frozenset[str] = frozenset("\\`*_~[<&#")

# CommonMark lets a backslash escape any ASCII punctuation character.
_ESCAPE: re.Pattern[str] = re.compile(rf"\\([{re.escape(string.punctuation)}])")


def as_markdown(text: str) -> str:
    """The Markdown that prints `text` as typed."""
    return "".join(
        f"\\{character}" if character in _STARTS_MARKDOWN else character
        for character in text
    )


def from_markdown(line: str) -> str:
    """The text a title or heading line prints: its escapes read."""
    return _ESCAPE.sub(r"\1", line)
