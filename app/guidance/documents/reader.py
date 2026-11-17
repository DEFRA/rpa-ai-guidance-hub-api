"""Read a stored guidance document back into the model that wrote it.

The inverse of `MarkdownDocument.markdown()`, and the reason a stored document can be
one plain Markdown file rather than a file and a description of itself. Everything the
model holds is either printed in the document or derived from what is: the hierarchy is
the heading depths, a section's ordinal is its position among the siblings of its own
kind, and its number is computed from those two - so nothing has to be written down
beside the Markdown to be read back out of it.

Two things do not survive, and both are stated here rather than left to be discovered:

**The names Word gave its bookmarks.** A cross-reference is stored resolved, pointing
at the anchor of the heading it means, so `_Toc178312345` is gone. Nothing needs it:
what a cross-reference requires is a stable handle on the section it points at, and the
anchor is one. The bookmarks rebuilt here are keyed by anchor, which puts the link back
in the same late-bound state the parser leaves it in - so a section moved after loading
renumbers, and every reference to it follows.

**The spacing of a pipe table.** `alignment.aligned` pads every cell out to its column
at render time, and a table read back keeps that padding rather than the single spaces
`tables` wrote. It costs nothing: `aligned` measures a cell only after stripping it, so
the padding carried here is discarded and recomputed on the next render, and a document
written, read and written again is byte for byte the document it was. What it means is
that `section.content` holds the shape the *file* is in rather than the shape the parser
first produced.

Everything else round-trips, and `content_type` is the one field read from a name rather
than from the document: the extension is the part's own, carried into the digest name
the picture is stored under.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass

from app.guidance.parsing import anchors, models

# What a section heading opens with: two or more hashes, because one is the
# document's title. The level is one less than the depth `markdown` writes, which
# adds one so that a top-level section sits beneath the title rather than beside it.
_SECTION_HASHES = 2

# The title line, whose text is empty in a document whose cover named none.
_TITLE = re.compile(r"^#\s?(.*)$")

_DEFAULT_CONTENT_TYPE = "application/octet-stream"

# A picture as a stored document writes it: image syntax, then whatever address the
# document was rendered with. The `!` is what tells it from an ordinary link.
_IMAGE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)\)")


def from_markdown(text: str) -> models.MarkdownDocument:
    """The document `text` renders, as the model that would render it again.

    Nothing has to be told to this. A picture's name is the last segment of whatever
    the document points at, so `assets/a3f9.png` and
    `s3://a-bucket/01JBQ8/a3f9.png` both name `a3f9.png` and a stored document can be
    read without knowing where it was written for - which is the whole point of the
    file carrying absolute addresses.
    """
    lines = text.split("\n")
    title = _title_of(lines)
    sections = _sections_of(lines)

    return models.MarkdownDocument(
        title=title,
        sections=sections,
        bookmarks=_bookmarks_of(sections),
    )


def _heading(line: str) -> tuple[str, str] | None:
    """A heading line as the hashes it opens with and the text it prints, or None.

    Hashes, whitespace, then the text - and `str.lstrip` and `str.strip` say exactly
    that in the order a reader reads it, so the rule needs no pattern and cannot be
    written as a slow one. A run of hashes with nothing after it is not a heading,
    and neither is one whose hashes run straight into a word: both are content that
    happens to begin with a `#`.
    """
    hashes = line[: len(line) - len(line.lstrip("#"))]
    printed = line[len(hashes) :]

    if len(hashes) < _SECTION_HASHES or not printed[:1].isspace():
        return None

    return hashes, printed.strip()


def _title_of(lines: list[str]) -> str:
    """The document's title: the first line, and only ever the first line.

    Read positionally rather than by searching for a `#`, because a line of content
    can begin with one and the title is written where it cannot be mistaken.
    """
    if not lines:
        return ""

    matched = _TITLE.match(lines[0])
    if not matched or _heading(lines[0]):
        return ""

    return matched.group(1).strip()


@dataclass
class _OpenSection:
    """A section that later headings may still be nested beneath.

    Mirrors `parser._OpenSection`: appendices and numbered sections are counted
    apart, so an annex following section 7 is A rather than 8 and the numbered
    heading after that annex is 8. Reading the counts back the way they were written
    is what lets an ordinal be recovered without trusting the number printed on the
    page.
    """

    section: models.MarkdownSection | None = None
    numbered: int = 0
    appendices: int = 0


def _sections_of(lines: list[str]) -> list[models.MarkdownSection]:
    """Every section of the document, in the order it prints them."""
    sections: list[models.MarkdownSection] = []
    stack = [_OpenSection()]

    for start, hashes, heading in _heading_lines(lines):
        level = len(hashes) - 1
        del stack[level:]

        section = _opened_beneath(stack[-1], heading)
        section.content = _unprefixed(_body(lines, start), section.images)

        sections.append(section)
        stack.append(_OpenSection(section))

    return sections


def _heading_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    """Each heading's line number, its hashes and the text it prints."""
    headings = ((index, _heading(line)) for index, line in enumerate(lines))
    return [(index, *matched) for index, matched in headings if matched is not None]


def _body(lines: list[str], start: int) -> str:
    """The content printed under the heading on line `start`.

    Bounded by the next heading rather than by a blank line, because a section's
    content holds blank lines of its own between its blocks.
    """
    end = start + 1
    while end < len(lines) and not _heading(lines[end]):
        end += 1

    return "\n".join(lines[start + 1 : end]).strip("\n")


def _opened_beneath(parent: _OpenSection, printed: str) -> models.MarkdownSection:
    """The section whose heading prints `printed`, opened beneath `parent`.

    Which kind of section it is, is decided by regenerating the number a numbered
    section here *would* print and asking whether the page prints it. Matching the
    expected number rather than parsing whatever number is there is what keeps an
    appendix headed "2024 rates" from being read as a numbered section: the answer
    is not "does this start with a number" but "does this start with *its* number".
    """
    expected = _next_number(parent)

    if printed.startswith(f"{expected} "):
        parent.numbered += 1
        section = models.MarkdownSection(
            heading=printed[len(expected) + 1 :],
            ordinal=parent.numbered,
            parent=parent.section,
        )
    else:
        parent.appendices += 1
        section = models.MarkdownSection(
            heading=printed,
            ordinal=parent.appendices,
            appendix=True,
            parent=parent.section,
        )

    # The link is made both ways here, as the parser makes it, so that "has no
    # parent" and "is in no section's children" stay one fact.
    if parent.section is not None:
        parent.section.children.append(section)

    return section


def _next_number(parent: _OpenSection) -> str:
    """The number the next numbered section beneath `parent` would print."""
    ordinal = parent.numbered + 1
    if parent.section is None:
        return str(ordinal)
    return f"{parent.section.number}.{ordinal}"


def _unprefixed(content: str, images: list[models.Image]) -> str:
    """Image paths back to bare names, recording each picture on the section.

    The name is the last segment of the path, whatever stands in front of it: a
    relative `assets/`, an `s3://` address, or nothing at all. Matched as image
    syntax rather than as "a link with a path in it", so a link to a document
    elsewhere is not read as a picture.
    """

    def bare(match: re.Match[str]) -> str:
        name = match.group(2).rsplit("/", 1)[-1]
        images.append(
            models.Image(
                name=name,
                content_type=mimetypes.guess_type(name)[0] or _DEFAULT_CONTENT_TYPE,
            )
        )
        return f"{match.group(1)}{name})"

    return _IMAGE.sub(bare, content)


def _bookmarks_of(
    sections: list[models.MarkdownSection],
) -> dict[str, models.MarkdownSection]:
    """Every section against the anchor its heading currently renders.

    Keyed by anchor because that is what the stored links say, and holding every
    section rather than only the ones something points at: which of them a link
    names is a question about the content, and answering it here would mean reading
    the content twice to learn nothing.
    """
    of_section = anchors.of_document(sections)
    return {of_section[id(section)]: section for section in sections}
