"""The words a reader sees in each section of a guide: the text a search matches, and
in which its matches are placed; and the links in each section.

A section starts at a heading of two or more `#`s that stands on its own, not in a
quote, a list or a code block, and runs to the next such heading; the hub's pages
split a guide the same way. It is known by its anchor: its heading as the hub's page
names it (`_as_named`), as a slug (`anchors.slugify`), a repeat numbered `-1`, `-2` in
order. Its text is its heading's
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
_COLOUR_MARKS = ("colour_span_open", "colour_span_close")


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
            sections.append((_numbered(_as_named(tokens[at + 1]), seen), lines))
        line = _readable(token)
        if line:
            lines.append(line)

    found = [SectionText(None, "\n".join(introduction))] if introduction else []
    return found + [SectionText(anchor, "\n".join(text)) for anchor, text in sections]


@dataclasses.dataclass(frozen=True)
class Link:
    """A link in a guide: its section's anchor (None before the first section), its
    order there counting from 1, its words, and where it goes, as written."""

    section: str | None
    order: int
    text: str
    destination: str


def links_of(markdown: str) -> list[Link]:
    """Every link in the guide, in order. A picture is not a link."""
    tokens = _reader().parse(markdown)
    section: str | None = None
    seen: dict[str, int] = {}
    counts: dict[str | None, int] = {}
    links: list[Link] = []

    for at, token in enumerate(tokens):
        if _starts_section(token):
            section = _numbered(_as_named(tokens[at + 1]), seen)
        for destination, text in _links_in(token.children or []):
            counts[section] = counts.get(section, 0) + 1
            links.append(Link(section, counts[section], text, destination))

    return links


@dataclasses.dataclass(frozen=True)
class LinkAddress:
    """Where a link goes, and where its address is written in the guide: from `start`
    up to `end`, as offsets in the Markdown."""

    destination: str
    start: int
    end: int


def link_addresses(markdown: str) -> list[LinkAddress]:
    """Every link that has an address of its own, in order; a reference-style link has
    none. The parser notes where each address is in its block's inline text, which
    lacks the block's indents, quote marks and table pipes; the address is the same
    occurrence of the same text in the block's lines, counted from the start."""
    starts = _line_starts(markdown)
    read: dict[tuple[int, int], str] = {}
    found: list[LinkAddress] = []
    for token in _reader().parse(markdown):
        if token.type != "inline" or token.map is None:
            continue
        first, end = token.map
        before = read.get((first, end), "")
        block = markdown[starts[first] : starts[end]]
        for child in token.children or []:
            span = child.meta.get(_ADDRESS) if child.type == "link_open" else None
            if span is None:
                continue
            written = token.content[span[0] : span[1]]
            nth = len(_places(before + token.content[: span[0]], written))
            places = _places(block, written)
            if nth < len(places):
                at = starts[first] + places[nth]
                found.append(
                    LinkAddress(str(child.attrGet("href")), at, at + len(written))
                )
        read[(first, end)] = f"{before}{token.content}\n"
    return found


def _places(text: str, part: str) -> list[int]:
    """Where `part` starts in `text`, each time."""
    places: list[int] = []
    at = text.find(part)
    while at >= 0:
        places.append(at)
        at = text.find(part, at + 1)
    return places


def _line_starts(text: str) -> list[int]:
    """Where each line starts, as the parser counts lines (a line ends at `\r\n`, `\r`
    or `\n`), and the end of the text."""
    starts = [0]
    at = 0
    while at < len(text):
        if text[at] not in "\r\n":
            at += 1
            continue
        at += 2 if text.startswith("\r\n", at) else 1
        starts.append(at)
    if starts[-1] != len(text):
        starts.append(len(text))
    return starts


def _links_in(children: list[md_token.Token]) -> list[tuple[str, str]]:
    """Each link in a run of inline tokens: where it goes, and its words."""
    found: list[tuple[str, str]] = []
    for at, child in enumerate(children):
        if child.type == "link_open":
            close = next(
                end
                for end in range(at + 1, len(children))
                if children[end].type == "link_close"
            )
            words = "".join(_words(token) for token in children[at + 1 : close])
            found.append((str(child.attrGet("href")), " ".join(words.split())))
    return found


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


def _written(token: md_token.Token) -> str:
    """An inline token's words, every space kept."""
    return "".join(_words(child) for child in token.children or []).strip()


def _as_named(token: md_token.Token) -> str:
    """A heading as the hub's page names it. The page's parser knows Markdown's own
    marks, but not HTML or the dialect's colours. So it keeps a link's words but not
    where it goes, a picture's words, and every space; and it keeps an entity, a tag
    and a colour's marks as written (`&amp;`, `<u>`, `[…]{.red}`)."""
    return "".join(_as_page_reads(child) for child in token.children or []).strip()


def _as_page_reads(token: md_token.Token) -> str:
    """A part of a heading as the hub's page reads it (`_as_named`)."""
    if token.type == "image":
        return "".join(_words(child) for child in token.children or [])
    if token.type == "html_inline":
        return token.content
    if token.info == "entity" or token.type in _COLOUR_MARKS:
        return token.markup
    return _words(token)


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
        words = _written(token)
    elif token.type in ("fence", "code_block"):
        words = token.content
    else:
        words = ""
    return " ".join(words.split())


def _words(token: md_token.Token) -> str:
    """The words a token shows: none for a mark or a picture, a space for a line
    break."""
    if token.type in ("text", "text_special", "code_inline"):
        return token.content
    if token.type in ("softbreak", "hardbreak"):
        return " "
    return " " if token.type == "html_inline" and _is_break(token.content) else ""


def _is_break(tag: str) -> bool:
    return tag.lower().startswith("<br")


class _Reader(markdown_it.MarkdownIt):
    """A parser that reads a `file:` link, a network share, as a link like any
    other, so that its address is not read as words; and keeps each link where it
    goes as the author wrote it, not encoded again."""

    def validateLink(self, url: str) -> bool:  # noqa: N802 - markdown-it's name
        return url.strip().lower().startswith("file:") or normalize_url.validateLink(
            url
        )

    def normalizeLink(self, url: str) -> str:  # noqa: N802 - markdown-it's name
        return url


@functools.cache
def _reader() -> markdown_it.MarkdownIt:
    # Each entity kept a token of its own (`text_join` would merge it into its text),
    # so that a heading's anchor can have it as written.
    parser = (
        _Reader("commonmark", {"html": True})
        .enable(["table", "strikethrough"])
        .disable("text_join")
    )
    parser.inline.ruler.before("link", "colour_span", _colour_span)
    parser.inline.ruler.at("link", _link_noting_address)
    return parser


# Where a link's address is in its block's inline text, on its opening token.
_ADDRESS = "address"


def _link_noting_address(state: rules_inline.StateInline, silent: bool) -> bool:  # noqa: FBT001 - markdown-it's rule signature
    """markdown-it's own link rule, noting on the link's opening token where its
    address is written: the start and end of it in the inline text."""
    start, pushed = state.pos, len(state.tokens)
    if not rules_inline.link(state, silent):
        return False
    if not silent:
        opening = next(
            token for token in state.tokens[pushed:] if token.type == "link_open"
        )
        span = _address_after(state, start)
        if span is not None:
            opening.meta[_ADDRESS] = span
    return True


def _address_after(
    state: rules_inline.StateInline, start: int
) -> tuple[int, int] | None:
    """Where the address of the link whose words start at `start` is written, or None
    for a reference-style link, which has no address of its own."""
    label_end = state.md.helpers.parseLinkLabel(state, start, True)
    at = label_end + 1
    if label_end < 0 or at >= state.posMax or state.src[at] != "(":
        return None
    at += 1
    while at < state.posMax and state.src[at] in " \t\n":
        at += 1
    destination = state.md.helpers.parseLinkDestination(state.src, at, state.posMax)
    return (at, destination.pos) if destination.ok else None


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
    state.push("colour_span_open", "span", 1).markup = "["
    state.md.inline.tokenize(state)
    state.push("colour_span_close", "span", -1).markup = "]" + colours.marker(colour)
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
