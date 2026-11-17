"""The words a reader sees in each section of a guide: the text a search matches, and
in which its matches are placed.

A section starts at a heading of two or more `#`s that stands on its own, not in a
quote, a list or a code block, and runs to the next such heading; the hub's pages
split a guide the same way. It is known by its anchor: its heading's words as a slug
(`anchors.slugify`), a repeat numbered `-1`, `-2` in order. Its text is its heading's
words, then the words of each of its blocks (a paragraph, a list item, a table's cell,
a code block), one block to a line, with one space between words. A link's words are
kept, and where it goes is not; a picture has no words. The text before the first
section heading is the guide's introduction, which has no anchor. The guide's title,
a first heading of one `#` when it is the only one, is in no section.

The Markdown is read with a parser, `markdown-it-py`, and a rule of its own for the
dialect's coloured span (`[text]{.red}`), never by lines or with a pattern.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import TYPE_CHECKING

import markdown_it
from markdown_it import rules_inline
from markdown_it.common import normalize_url

from app.guidance.parsing import anchors, colours

if TYPE_CHECKING:
    from markdown_it import token as md_token

_COLOURS = (colours.RED, colours.BLUE)


@dataclasses.dataclass(frozen=True)
class SectionText:
    """A section's anchor (None for the introduction) and its words, as the module
    docstring says."""

    anchor: str | None
    text: str


def sections_of(markdown: str) -> list[SectionText]:
    """Every section of the guide, in order, the introduction first if it has one."""
    tokens = _reader().parse(markdown)
    title_ends = _title_end(tokens)
    introduction: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    lines = introduction
    seen: dict[str, int] = {}

    for at, token in enumerate(tokens):
        if title_ends is not None and at <= title_ends:
            continue
        if _starts_section(token):
            lines = []
            sections.append((_numbered(_readable(tokens[at + 1]), seen), lines))
        line = _readable(token)
        if line:
            lines.append(line)

    found = [SectionText(None, "\n".join(introduction))] if introduction else []
    return found + [SectionText(anchor, "\n".join(text)) for anchor, text in sections]


def _starts_section(token: md_token.Token) -> bool:
    """Whether a token opens a heading of two or more `#`s that stands on its own."""
    return (
        token.type == "heading_open"
        and token.level == 0
        and token.markup.startswith("##")
    )


def _title_end(tokens: list[md_token.Token]) -> int | None:
    """Where the guide's title ends: its first block, when that is a heading of one
    `#` and the only one. None for a guide with no title."""
    top_headings = [
        at
        for at, token in enumerate(tokens)
        if token.type == "heading_open" and token.level == 0 and token.tag == "h1"
    ]
    if top_headings != [0]:
        return None
    return next(at for at, token in enumerate(tokens) if token.type == "heading_close")


def _numbered(heading: str, seen: dict[str, int]) -> str:
    """A heading's anchor: its words as a slug, numbered when it repeats one before."""
    base = anchors.slugify(heading)
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def _readable(token: md_token.Token) -> str:
    """The words a block token shows, one space between them: an inline token's (a
    heading's, a paragraph's, a cell's), or a code block's; none for any other."""
    if token.type == "inline":
        words = "".join(_words(child) for child in token.children or [])
    elif token.type in ("fence", "code_block"):
        words = token.content
    else:
        words = ""
    return " ".join(words.split())


def _words(token: md_token.Token) -> str:
    """The words a token shows: none for a mark or a picture, a space for a line
    break."""
    if token.type in ("text", "code_inline"):
        return token.content
    if token.type in ("softbreak", "hardbreak"):
        return " "
    return " " if token.type == "html_inline" and _is_break(token.content) else ""


def _is_break(tag: str) -> bool:
    return tag.lower().startswith("<br")


class _Reader(markdown_it.MarkdownIt):
    """A parser that reads a `file:` link, a network share, as a link like any
    other, so that its address is not read as words."""

    def validateLink(self, url: str) -> bool:  # noqa: N802 - markdown-it's name
        return url.strip().lower().startswith("file:") or normalize_url.validateLink(
            url
        )


@functools.cache
def _reader() -> markdown_it.MarkdownIt:
    parser = _Reader("commonmark", {"html": True}).enable(["table", "strikethrough"])
    parser.inline.ruler.before("link", "colour_span", _colour_span)
    return parser


def _colour_span(state: rules_inline.StateInline, silent: bool) -> bool:  # noqa: FBT001 - markdown-it's rule signature
    """`[text]{.red}`: a span of colour, its text read as Markdown in turn.

    Matched only when making tokens. While a link's text is being measured
    (`silent`), a span read as a token would count as a link inside the link and end
    it; left as the brackets it is made of, which balance, the link is found whole,
    and its text is then read for tokens, span and all.
    """
    if silent or state.src[state.pos] != "[":
        return False

    end = state.md.helpers.parseLinkLabel(state, state.pos)
    colour = None if end < 0 else _colour_after(state.src, end + 1)
    if colour is None:
        return False

    maximum = state.posMax
    state.pos += 1
    state.posMax = end
    state.push("colour_span_open", "span", 1)
    state.md.inline.tokenize(state)
    state.push("colour_span_close", "span", -1)
    state.posMax = maximum
    state.pos = end + 1 + len(colours.marker(colour))
    return True


def _colour_after(source: str, at: int) -> str | None:
    """The colour whose marker is at `at`, or None for none."""
    return next(
        (
            colour
            for colour in _COLOURS
            if source.startswith(colours.marker(colour), at)
        ),
        None,
    )
