"""The first paragraph of a section, as plain text: the words that open it, which the
hub shows under a bookmarked section's heading.

Read with plain code over the Markdown the parser writes (`docs/markdown-dialect.md`):
blocks between blank lines, and the inline marks the dialect has. It is not a general
Markdown reader.
"""

from __future__ import annotations

import html

# A fenced code block opens and closes with this.
_FENCE = "```"

# Marks in a paragraph's text that are not words: emphasis, strikethrough, code. The
# parser escapes any of them that is the author's own text.
_MARKS = frozenset("*_~`")

# What opens a list item or a quotation, at the start of a line.
_BULLETS = ("- ", "* ", "+ ", "> ")

# What follows a link's or a coloured span's text: its address, or its class.
_AFTER_TEXT = {"(": ")", "{": "}"}


def first_of(content: str) -> str | None:
    """The first block of words in a section's content, as plain text: not a table, a
    code block or a picture alone. None for a section with no words of its own."""
    for block in _blocks(content.split("\n")):
        if block[0].startswith(("|", _FENCE)):
            continue
        text = " ".join(plain_text(_unbulleted(line)) for line in block).strip()
        if text:
            return text
    return None


def plain_text(markdown: str) -> str:
    """The words of a line of the dialect's Markdown, without its marks: a link or a
    coloured span is its text, a picture nothing, a tag nothing (a line break a space),
    an escape the character, and an entity the character it names."""
    words: list[str] = []
    at = 0
    while at < len(markdown):
        read, at = _read_at(markdown, at)
        words.append(read)
    return html.unescape(" ".join("".join(words).split()))


def _read_at(markdown: str, at: int) -> tuple[str, int]:
    """The words of whatever starts at `at`, and where the next thing starts."""
    character = markdown[at]
    if character == "\\" and at + 1 < len(markdown):
        return markdown[at + 1], at + 2
    if character == "!" and markdown[at + 1 : at + 2] == "[":
        return _picture(markdown, at)
    if character == "[":
        return _bracketed(markdown, at)
    if character == "<":
        return _tag(markdown, at)
    if character in _MARKS:
        return "", at + 1
    return character, at + 1


def _picture(markdown: str, at: int) -> tuple[str, int]:
    """A picture is no words; a `!` that opens none is itself."""
    end = _closing(markdown, at + 1, "[", "]")
    after = end + 1
    if end == -1 or markdown[after : after + 1] != "(":
        return "!", at + 1
    return "", max(_closing(markdown, after, "(", ")"), after) + 1


def _bracketed(markdown: str, at: int) -> tuple[str, int]:
    """A link or a coloured span is its text, without its address or its class; a `[`
    that closes nowhere is itself."""
    end = _closing(markdown, at, "[", "]")
    if end == -1:
        return "[", at + 1
    text = plain_text(markdown[at + 1 : end])
    after = end + 1
    closing = _AFTER_TEXT.get(markdown[after : after + 1])
    if closing is None:
        return text, after
    return text, _closing(markdown, after, markdown[after], closing) + 1


def _tag(markdown: str, at: int) -> tuple[str, int]:
    """A tag is no words, but a line break is a space; a `<` that closes nowhere is
    itself."""
    end = markdown.find(">", at)
    if end == -1:
        return "<", at + 1
    is_break = markdown[at : end + 1].lower().startswith("<br")
    return (" " if is_break else ""), end + 1


def _closing(text: str, start: int, opening: str, closing: str) -> int:
    """Where the bracket opened at `start` closes, or -1; escapes and nesting
    counted."""
    depth = 0
    at = start
    while at < len(text):
        character = text[at]
        if character == "\\":
            at += 2
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return at
        at += 1
    return -1


def _blocks(lines: list[str]) -> list[list[str]]:
    """The lines in blocks, split at blank lines; a code block is one block, blank
    lines and all."""
    blocks: list[list[str]] = [[]]
    fenced = False
    for line in lines:
        if line.strip().startswith(_FENCE):
            fenced = not fenced
        if line.strip() or fenced:
            blocks[-1].append(line.strip())
        elif blocks[-1]:
            blocks.append([])
    return [block for block in blocks if block]


def _unbulleted(line: str) -> str:
    """A line without the marker that opens a list item or a quotation."""
    for bullet in _BULLETS:
        if line.startswith(bullet):
            return line[len(bullet) :]
    number, dot, rest = line.partition(". ")
    return rest if dot and number.isdigit() else line
