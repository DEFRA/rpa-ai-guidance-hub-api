#!/usr/bin/env python3
"""Report what the guidance parser loses when it renders a .docx as Markdown.

Reads the Word document twice over: once directly, for what Word would put on the
page, and once through ``parser.parse_docx`` for the Markdown the API would really
store. Each side is reduced to a counted bag of symbols per section, and the report
is the share of the Word bag the Markdown bag covers.

Three kinds of symbol are counted. Words and URLs ask whether the document still
says what it said. Marks ask whether it still *looks* how it looked: a mark is one
word wearing one feature -- bold, a colour, a link, the list or table or box it sits
in -- so a conversion earns its score only by marking up the same text the document
does. The last column of the feature table is the other half of that question,
counting marks the Markdown wears that the document never asked for.

The two sides read the same document through entirely separate code, and that is the
point of the instrument rather than duplication to be tidied away: an audit sharing
the parser's assumptions could not find a fault in them. Nothing below reads a rule
from ``app.guidance.parsing``, and neither should anything added to it.

The cover page and the table of contents are excluded: the audit begins at the
first body heading, which is also where the parser's own sections begin.

Nothing but python-docx and the parser is involved -- no configuration, database,
S3 or Bedrock access -- so this runs against any document on disk.

``--tiptap`` adds a third leg. The guidance editor's schema cannot model everything
the parser can write, so a document is changed again the first time anyone opens and
saves it; given that round trip's Markdown, the report scores it against the same
Word side and says which marks survive being stored *and* edited. Producing it needs
the UI repository, so the flag takes a file rather than making one -- the
orchestrator's ``audit_doc.py`` is what assembles the leg.

Usage:
  uv run scripts/audit_docx.py <document.docx> [--tiptap FILE] [--missing] [--top N]

Called directly, or by ``scripts/audit_doc.py`` in the local-dev orchestrator
repository, which resolves paths and audits several documents at once.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any

import docx
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError

if TYPE_CHECKING:
    from collections.abc import Iterator

    import docx.document

    from app.guidance.parsing import models

# A URL as an author would write one, stopping before the punctuation that ends the
# sentence carrying it rather than swallowing it into the address.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'\]\)}]+", re.IGNORECASE)

# A word is a run of alphanumerics, kept whole across the apostrophes and hyphens
# inside it, so "person's" and "re-check" are each one symbol rather than two.
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")

# The formatting vocabulary the two sides are reduced to.
#
# Neither Word's names nor Markdown's, but one set of words for what a *reader* sees,
# which is the whole of what makes the sides comparable. Word draws a box either as a
# one-cell table or as a text box, and Markdown draws one as a blockquote: all three
# are BOX. Where the two already agree, as they do on bold, the shared name is simply
# the obvious one.
#
# Heading depth is deliberately absent. The parser derives its levels *relatively* - a
# document opening at Heading 2 still starts at depth 1 - so a Word level and a
# Markdown level are not the same measurement, and scoring one against the other would
# report a loss where the renumbering is doing exactly what it is meant to.
BOLD = "bold"
ITALIC = "italic"
UNDERLINE = "underline"
STRIKETHROUGH = "strikethrough"
SUPERSCRIPT = "superscript"
SUBSCRIPT = "subscript"
LINK = "link"
LIST = "list"
NUMBERED = "numbered"
TABLE = "table"
BOX = "box"
IMAGE = "image"

# Colour names are the document's own vocabulary rather than this module's: what the
# Markdown carries is `{.red}`, so the audit has to be able to say "red" too.
RED = "red"
BLUE = "blue"
_COLOURS = frozenset({RED, BLUE})

# Report order: the marks a run wears, then the blocks text sits in.
_FEATURES = (
    BOLD,
    ITALIC,
    UNDERLINE,
    STRIKETHROUGH,
    SUPERSCRIPT,
    SUBSCRIPT,
    RED,
    BLUE,
    LINK,
    LIST,
    NUMBERED,
    TABLE,
    BOX,
    IMAGE,
)

# The mark a picture wears. A picture has no words, so it is counted under the empty
# one - which no word pattern can produce, and so cannot collide with a real word.
_NO_WORD = ""

# Markup compatibility, which python-docx's namespace map does not carry.
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_ALTERNATE_CONTENT = f"{_MC}AlternateContent"
_CHOICE = f"{_MC}Choice"
_FALLBACK = f"{_MC}Fallback"

_WHITESPACE_RUN = re.compile(r"\s+")

# A link field's instruction, e.g. `HYPERLINK "https://example.org/a"`.
_HYPERLINK_FIELD = re.compile(r'HYPERLINK\s+"([^"]*)"', re.IGNORECASE)

# Trailing characters a URL can pick up from the prose around it.
_URL_TRAILING = ".,;:!?)]}'\"’"

# ---------------------------------------------------------------------------
# What Word says about a mark
# ---------------------------------------------------------------------------

_W_VAL = "w:val"

# The contents of a box Word drew with the drawing tools, as opposed to with a
# one-cell table. Both are boxes to a reader, so both are BOX.
_TEXT_BOX = qn("w:txbxContent")

# What a text box does not inherit from the paragraph it hangs off: the marks that
# paragraph's runs wear, and the list it is an item of. A box is a story of its own -
# its paragraphs and runs carry their own properties, and Word applies none of the
# anchor's to them. It draws no bullet on a box either, whatever the anchor's own
# numbering says, which is also the answer the same box gets when it is drawn as a
# one-cell table instead: those paragraphs are reached through the table and are
# never asked what list the anchor was in.
#
# Left in, one red anchor paints a whole case note red and one bulleted anchor makes
# a list of it - and, this side being the oracle, the parser is reported as having
# lost marks the page never drew.
#
# What is *not* dropped is the block the box sits in. A box inside a table is inside
# that table however it was drawn, and BOX itself is added on the way past.
_NOT_INHERITED_BY_A_BOX = frozenset(
    {
        BOLD,
        ITALIC,
        UNDERLINE,
        STRIKETHROUGH,
        SUPERSCRIPT,
        SUBSCRIPT,
        RED,
        BLUE,
        LINK,
        LIST,
        NUMBERED,
    }
)

# Elements that separate the text either side of them without printing a word.
_SEPARATORS = (qn("w:tab"), qn("w:br"), qn("w:cr"))

_VERTICAL_MARKS = {"superscript": SUPERSCRIPT, "subscript": SUBSCRIPT}

# Word paints its Hyperlink style over both of these, so on a link they are the
# renderer's decision rather than the author's - and what they were saying, the link
# itself now says. Left in, every link in the document would score a lost underline.
_PAINTED_BY_LINK = frozenset({UNDERLINE, RED, BLUE})

# A numId of zero is numbering taken *off* a paragraph rather than a list it is in.
_NUMBERING_REMOVED = "0"

# The one list format that is not a number.
_BULLET_FORMAT = "bullet"

# A hex colour, which is the only thing worth reading a colour from: Word also writes
# "auto", meaning "whatever contrasts with the background", which is a deferral.
_HEX_LENGTH = 6
_CHANNEL = 2

# ---------------------------------------------------------------------------
# What Markdown says about a mark
# ---------------------------------------------------------------------------

# A run of these characters opens and closes emphasis, and how long the run is says
# which. Three asterisks is one span wearing both marks, not a bold next to an italic.
_EMPHASIS_MARKS = {
    ("*", 1): frozenset({ITALIC}),
    ("*", 2): frozenset({BOLD}),
    ("*", 3): frozenset({BOLD, ITALIC}),
    ("~", 2): frozenset({STRIKETHROUGH}),
}

# The inline HTML the parser writes where Markdown has no marker of its own.
_TAG_MARKS = {"u": UNDERLINE, "sup": SUPERSCRIPT, "sub": SUBSCRIPT}
_OPEN_TAG = re.compile(r"<(u|sup|sub)>", re.IGNORECASE)

# A line break: inside a row it separates the words either side of it just as a w:br
# does, so the two sides agree on where a word ends by construction. It is also what
# `tables` joins a cell's blocks with, a row having no room for a newline, so it is
# where a cell is split back into the blocks it was made of.
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)

# A link or image destination. It is wrapped in <> only where bare would not parse -
# whitespace, or brackets that do not balance - and written bare otherwise. That is
# CommonMark, and it is exactly what the parser's own `_destination` writes.
#
# The bare form therefore has to allow a balanced pair, which a filename in a path
# routinely carries. Reading it as "anything but a bracket" stops such an address at
# the "(" in its filename and scores an address the Markdown states in full as lost.
_DESTINATION = re.compile(r"\((?:<([^>]*)>|((?:[^()\s]|\([^()\s]*\))*))[^)]*\)")

# The attribute that makes a bracketed span a coloured one: the `{.red}` of
# `[text]{.red}`. The name is the mark; the brackets are punctuation the word pattern
# skips, so nothing has to strip them.
_SPAN_ATTRIBUTE = re.compile(r"\{\.([a-z]+)\}")

# Block markers. A quote marker and a list marker each claim a whole line; a row of
# pipes is a table row only under the delimiter that declares one.
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE_MARKER = re.compile(r"^\s{0,3}>\s?")
_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_DELIMITER_ROW = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")

# A bullet is read as "-" or "+" and never as "*", which is also a valid bullet and
# is ambiguous where it matters: "*emphasis* opens the line" would otherwise be a
# list item. The parser writes "-", and no real guide opens a line the other way.
_BULLET_ITEM = re.compile(r"^\s*[-+][ \t]+")
_ORDERED_ITEM = re.compile(r"^\s*\d+\.[ \t]+")

# What opens a body section, and what is navigation rather than content.
#
# Stated here rather than imported from the parser on purpose. An instrument that
# shares the assumptions of the thing it measures cannot find a fault in them: if
# the parser stopped treating "Appendix" as a heading, importing its constants
# would make the audit stop looking for appendices too, and a whole annex of lost
# text would quietly leave the report instead of showing up as missing. These are
# meant to describe what Word puts on the page, so they should only ever change
# because a document does something new -- not because the parser did.
_BODY_STYLES = ("heading", "appendix", "annex", "schedule")
_CONTENTS_STYLES = ("toc", "contents", "table of contents")

# A contents page whose heading is styled as an ordinary heading, and so is named
# only by what it says.
_CONTENTS_HEADINGS = frozenset({"contents", "table of contents", "contents page"})

_LABEL_WIDTH = 44

# The six score columns after the label, and the widest feature name under them.
_COLUMNS_WIDTH = 48
_FEATURE_WIDTH = 16
_KEPT_WIDTH = 8

# Wide enough for "strikethrough:", the longest label a --missing line can open with.
_MARK_LABEL_WIDTH = 15

_DEFAULT_TOP = 12


@dataclass
class Bag:
    """The counted symbols of one section: its words, its URLs and its marks.

    Three counters rather than one because a document with many links, or a heavily
    formatted one, should not have its prose score moved by them in either direction.

    A mark is counted as a *pair* - the feature and one word wearing it - so that the
    score answers "is this text bold in both" rather than "are there as many bold
    things in both". A bold count alone passes a conversion that bolds the wrong
    sentence; a bag of `(bold, word)` scores it as the loss it is. Counting words
    rather than spans is what makes the two sides comparable at all: Word starts a
    new run wherever anything changes, mid-word included, and the parser merges
    neighbours back together, so no count of spans could ever agree.
    """

    words: Counter[str] = field(default_factory=Counter)
    urls: Counter[str] = field(default_factory=Counter)
    marks: Counter[tuple[str, str]] = field(default_factory=Counter)

    # Normalised URL back to the way the document first wrote it. The counters are
    # keyed on the normalised form so the two sides agree, but a URL is only useful
    # in the report if it is quoted as the author typed it.
    forms: dict[str, str] = field(default_factory=dict)

    def add_text(self, text: str) -> None:
        """Fold a piece of rendered text into the bag."""
        for match in _URL.finditer(text):
            self.add_url(match.group())
        self.words.update(_WORD.findall(_URL.sub(" ", text).lower()))

    def add_url(self, url: str) -> None:
        """Fold one URL into the bag, under the form both sides will agree on."""
        key = normalise_url(url)
        self.urls[key] += 1
        self.forms.setdefault(key, url)

    def add_marks(self, text: str, features: frozenset[str]) -> None:
        """Fold one uniformly-marked stretch of text into the bag as marks.

        The stretch has to be a whole one before it arrives: a mark counted per run,
        or per fragment between two pieces of markup, splits the words at the seam
        and matches nothing the other side says.
        """
        if not features:
            return
        for word in _WORD.findall(_URL.sub(" ", text).lower()):
            for feature in features:
                self.marks[feature, word] += 1

    def add_image(self) -> None:
        """Fold one picture into the bag. A picture is a mark with no words."""
        self.marks[IMAGE, _NO_WORD] += 1

    def written(self, key: str) -> str:
        """How the document wrote the URL that normalised to `key`."""
        return self.forms.get(key, key)

    def __ior__(self, other: Bag) -> Bag:
        self.words.update(other.words)
        self.urls.update(other.urls)
        self.marks.update(other.marks)
        for key, url in other.forms.items():
            self.forms.setdefault(key, url)
        return self


@dataclass
class Section:
    """One section of the document as a bag of symbols, under its heading."""

    heading: str
    bag: Bag = field(default_factory=Bag)

    @property
    def key(self) -> str:
        """The form of the heading used to match the two sides up."""
        return normalise_heading(self.heading)


@dataclass
class Coverage:
    """How much of one bag a second bag accounts for."""

    total: int
    covered: int
    extra: int

    @property
    def fraction(self) -> float | None:
        """The share covered, or None when there was nothing to cover."""
        return self.covered / self.total if self.total else None


def normalise_url(url: str) -> str:
    """Reduce a URL to the form the Word and Markdown sides will both produce."""
    return url.rstrip(_URL_TRAILING).rstrip("/").lower()


def normalise_heading(heading: str) -> str:
    """Reduce a heading to a match key: case and spacing carry no meaning here."""
    return _WHITESPACE_RUN.sub(" ", heading).strip().lower()


def cover[Symbol](source: Counter[Symbol], rendered: Counter[Symbol]) -> Coverage:
    """Score how much of `source` `rendered` accounts for.

    The intersection is a multiset one, so repeats count: dropping three of five
    occurrences of a word scores that word at two fifths, and the Markdown earns
    nothing for words the document never said.

    The symbol is whatever the two sides agreed to count - a word, a URL, or the
    pair of a feature and the word wearing it. The rule is the same for all three,
    which is the point of reducing every measurement to a bag in the first place.
    """
    return Coverage(
        total=sum(source.values()),
        covered=sum((source & rendered).values()),
        extra=sum((rendered - source).values()),
    )


def marks_of(marks: Counter[tuple[str, str]], feature: str) -> Counter[str]:
    """The words wearing one feature, pulled out as a counter of their own."""
    return Counter({word: n for (name, word), n in marks.items() if name == feature})


def iter_blocks(document: docx.document.Document) -> Iterator[Paragraph | Table]:
    """Yield the body's paragraphs and tables in the order they are laid out.

    `document.paragraphs` skips tables entirely, and seven of them in a real guide
    is a great deal of text to score as never having existed.
    """
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def rendered_text(paragraph: Paragraph) -> str:
    """The text this paragraph puts on the page, in document order.

    Taken from the XML rather than `paragraph.text` so that what is counted is what
    is printed: text inside a hyperlink or a text box is included, while a tracked
    deletion (`w:delText`) and a field's instructions (`w:instrText`) are other
    elements entirely and so are left out by construction. Adjacent `w:t` runs join
    without a space -- Word splits a single word across runs freely -- while a tab
    or a line break separates.
    """
    return element_text(paragraph._p)


def element_text(element: Any) -> str:
    """The text this element and everything beneath it puts on the page."""
    parts: list[str] = []
    for node in rendered_nodes(element):
        if node.tag == qn("w:t"):
            parts.append(node.text or "")
        elif node.tag in (qn("w:tab"), qn("w:br"), qn("w:cr")):
            parts.append(" ")
    return "".join(parts)


def rendered_nodes(element: Any) -> Iterator[Any]:
    """`element` and everything beneath it that reaches the page, in reading order.

    A plain `element.iter()` would do, were it not for `mc:AlternateContent`: that
    holds one copy of the same content per consumer that might read it, and Word
    prints exactly one of them. One text box written twice over, as DrawingML in
    `mc:Choice` and as VML in `mc:Fallback`, is 85 words of a real guide credited to
    a document that says them once -- and, because the audit is the oracle, 85 words
    the parser is then reported as having lost.

    This is the third defect of the family, after the `id(cell._tc)` reuse fixed in
    `c15122f` and the merged cells before it: text Word writes once being read more
    than once. Look here first when a section scores low with nothing visibly wrong
    in the Markdown.
    """
    pending = [element]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(rendered_children(node)))


def rendered_children(element: Any) -> list[Any]:
    """The children a reader sees: one branch of an alternate, all of anything else.

    The first `mc:Choice` is what Word takes when it understands the markup the
    branch requires, which for a modern document it does; `mc:Fallback` is what it
    falls back to and is the answer only when there is no choice at all. Evaluating
    `mc:Requires` properly would need a table of every namespace we can render, and
    neither guide holds an alternate with more than one choice.
    """
    if element.tag != _ALTERNATE_CONTENT:
        return list(element)

    branch = element.find(_CHOICE)
    if branch is None:
        branch = element.find(_FALLBACK)
    return [] if branch is None else [branch]


def hyperlink_urls(paragraph: Paragraph) -> Iterator[str]:
    """The targets of this paragraph's external hyperlinks, in both forms.

    A link's address lives in the relationships part or in a field's instruction,
    never in the text, so it is invisible to any amount of reading the paragraph.
    Links to a bookmark inside the document carry `w:anchor` and no relationship,
    and are not URLs. A link Word renders no text for puts nothing on the page, so
    it is not counted as one.

    The field walk deliberately repeats the parser's rather than sharing it: this
    side of the audit is the oracle, and a fault the two held in common would
    cancel itself out of the score.
    """
    relationships = paragraph.part.rels
    for link in paragraph._p.iter(qn("w:hyperlink")):
        relationship_id = link.get(qn("r:id"))
        if relationship_id in relationships and element_text(link).strip():
            yield relationships[relationship_id].target_ref

    yield from field_hyperlink_urls(paragraph)


def field_hyperlink_urls(paragraph: Paragraph) -> Iterator[str]:
    """The targets of this paragraph's legacy HYPERLINK fields.

    A field is a run sequence: a fldChar begin, the runs spelling the instruction,
    a separate, the runs Word rendered from it, and an end. The end is not always
    written -- one of the two real guides stops a paragraph mid-field -- so a field
    still open when the runs are exhausted counts as much as a closed one.
    """
    target = ""
    instruction = ""
    rendered = ""
    showing_result = False

    for run in paragraph._p.iter(qn("w:r")):
        boundary = fld_char_type(run)
        if boundary == "begin":
            target, instruction, rendered, showing_result = "", "", "", False
        elif boundary == "separate":
            target = hyperlink_field_target(instruction)
            showing_result = True
        elif boundary == "end":
            if target and rendered.strip():
                yield target
            target, rendered = "", ""
        elif showing_result:
            rendered += element_text(run)
        else:
            instruction += "".join(
                element.text or "" for element in run.iter(qn("w:instrText"))
            )

    if target and rendered.strip():
        yield target


def fld_char_type(run: Any) -> str:
    """Which end of a field this run marks, or "" for an ordinary run."""
    element = run.find(qn("w:fldChar"))
    if element is None:
        return ""
    return str(element.get(qn("w:fldCharType")) or "")


def hyperlink_field_target(instruction: str) -> str:
    """The address a HYPERLINK field points at, or "" for any other field."""
    match = _HYPERLINK_FIELD.match(instruction.strip())
    return match.group(1) if match else ""


@dataclass
class OpenField:
    """A Word field, as a walk goes past the runs that make it up.

    A field is a run sequence rather than an element of its own: a fldChar begin, the
    runs spelling the instruction that says what the field is, a separate, the runs
    Word rendered from that instruction, and an end. Instruction and result are both
    ordinary runs, so only the position in the walk tells them apart - which is why
    the walk has to carry the field rather than find it.

    Repeating `field_hyperlink_urls` rather than sharing it is the same deliberate
    duplication the rest of this file is: this side is the oracle, and a fault the two
    sides held in common would cancel itself out of the score.
    """

    instruction: str = ""
    target: str = ""

    def absorb(self, run: Any) -> str:
        """Take one run, and say what address the runs from here are wearing."""
        boundary = fld_char_type(run)
        if boundary == "begin":
            self.instruction, self.target = "", ""
        elif boundary == "separate":
            self.target = hyperlink_field_target(self.instruction)
        elif boundary == "end":
            self.target = ""
        elif not self.target:
            self.instruction += "".join(
                child.text or "" for child in run.iter(qn("w:instrText"))
            )
        return self.target


def mark_paragraph(bag: Bag, paragraph: Paragraph, features: frozenset[str]) -> None:
    """Fold the marks this paragraph wears into the bag, under `features`.

    `features` is what the paragraph's surroundings already say about it - the table
    or box it sits in - and every mark it wears itself is added to that.

    The walk gathers text into stretches wearing the same marks before any of it is
    counted, and that is not tidiness. Word starts a new run wherever anything at all
    changes, mid-word included, so counting each run's text on its own scores "claim"
    as "cl" and "aim" and matches nothing the Markdown could ever say. Merging
    neighbours that agree is the same rule the parser applies for the same reason,
    reached independently - see the module docstring on why nothing here is imported.

    Fields are read on the way past, in document order, exactly as
    `field_hyperlink_urls` reads them: the runs a HYPERLINK field renders wear a link
    that no element of the paragraph carries.

    The walk starts below the paragraph rather than at it, so that every w:p it meets
    is one Word wrote inside a box the paragraph anchors. Those have paragraph
    properties of their own, and a box holding a list says so there and nowhere the
    caller can see.

    Marks accumulate downwards, and a text box is where that stops: what a box holds
    is marked by neither the run the drawing hangs off nor the list its paragraph is
    an item of. The block it sits in still counts - a box inside a table is inside
    that table - so what is dropped is what the anchor was wearing and no more.
    """
    segments: list[tuple[frozenset[str], str]] = []
    field = OpenField()

    pending = [
        (child, features, False) for child in reversed(rendered_children(paragraph._p))
    ]
    while pending:
        element, active, in_link = pending.pop()
        tag = element.tag

        if tag == qn("w:t"):
            segments.append((active, element.text or ""))
            continue
        if tag in _SEPARATORS:
            segments.append((active, " "))
            continue

        if tag == qn("w:hyperlink"):
            in_link = True
        elif tag == _TEXT_BOX:
            active = (active - _NOT_INHERITED_BY_A_BOX) | {BOX}
            in_link = False
        elif tag == qn("w:p"):
            active = active | list_features(Paragraph(element, paragraph))
        elif tag == qn("w:r"):
            linked = in_link or bool(field.absorb(element))
            active = active | run_marks(element, in_link=linked)
            for _ in pictures(element):
                bag.add_image()

        pending.extend(
            (child, active, in_link) for child in reversed(rendered_children(element))
        )

    for marks, group in groupby(segments, key=lambda segment: segment[0]):
        bag.add_marks("".join(text for _, text in group), marks)


def run_marks(run: Any, *, in_link: bool) -> frozenset[str]:
    """What one run is wearing, as features.

    A link's underline and colour are dropped rather than counted. Word's Hyperlink
    style paints both on every link it renders, so they are the renderer speaking and
    not the author, and what they were saying the link itself now says. That is a
    statement about the page, which is this side's remit; the parser reaches the same
    conclusion separately, and neither reads it from the other.
    """
    marks = {LINK} if in_link else set()

    properties = run.find(qn("w:rPr"))
    if properties is not None:
        if is_toggle_on(properties.find(qn("w:b"))):
            marks.add(BOLD)
        if is_toggle_on(properties.find(qn("w:i"))):
            marks.add(ITALIC)
        if is_toggle_on(properties.find(qn("w:strike"))):
            marks.add(STRIKETHROUGH)
        if is_underlined(properties.find(qn("w:u"))):
            marks.add(UNDERLINE)
        marks |= vertical_mark(properties.find(qn("w:vertAlign")))
        marks |= colour_mark(properties.find(qn("w:color")))

    return frozenset(marks - _PAINTED_BY_LINK if in_link else marks)


def is_toggle_on(element: Any) -> bool:
    """Whether an OOXML boolean toggle is present and not explicitly disabled."""
    return element is not None and element.get(qn(_W_VAL)) not in ("false", "0")


def is_underlined(element: Any) -> bool:
    """Underline is not a toggle: it names a line to draw, and "none" is no line."""
    return element is not None and element.get(qn(_W_VAL)) != "none"


def vertical_mark(element: Any) -> frozenset[str]:
    """Whether the run is raised or lowered off the line it sits on."""
    if element is None:
        return frozenset()
    mark = _VERTICAL_MARKS.get(element.get(qn(_W_VAL)))
    return frozenset({mark}) if mark else frozenset()


def colour_mark(element: Any) -> frozenset[str]:
    """The colour the run is written in, matched to the nearer of red and blue.

    Every colour is read as the intent it approximates, so a shade an author reached
    for by hand is one of the two names the document's convention uses rather than a
    stray of its own. The comparison is red channel against blue: expand the squared
    distances to pure red and pure blue and every term cancels but the difference of
    those two, so the larger channel names the nearer colour exactly.

    Equal channels mean the colour lies between the two and names neither, which is
    one rule covering black, every grey, white, and the greens nothing here would
    know what to do with. It is also why the default needs no special case: Word
    spells the default out explicitly on most coloured runs, and it is achromatic.
    """
    value = None if element is None else element.get(qn(_W_VAL))
    if value is None or len(value) != _HEX_LENGTH:
        return frozenset()

    try:
        red = int(value[:_CHANNEL], 16)
        blue = int(value[-_CHANNEL:], 16)
    except ValueError:
        return frozenset()

    if red == blue:
        return frozenset()
    return frozenset({RED if red > blue else BLUE})


def pictures(run: Any) -> Iterator[Any]:
    """The pictures this run draws.

    Only the run's own w:drawing children are looked at, never its descendants, which
    is what keeps a text box out: Word wraps a text box's drawing in an
    mc:AlternateContent, so it is a grandchild. A drawing holding a shape rather than
    a picture has no a:blip and is no picture here.
    """
    for drawing in run.findall(qn("w:drawing")):
        for blip in drawing.iter(qn("a:blip")):
            if blip.get(qn("r:embed")):
                yield blip


def list_features(paragraph: Paragraph) -> frozenset[str]:
    """The list this paragraph is an item of, or nothing where it is not one.

    What makes a paragraph a list item is numbering being in effect on it, and
    nothing else - not its style's name, because an author can bullet a paragraph
    styled "Normal". Word generates the bullet or the digit when it draws the page,
    so neither is in the text and neither can be read from it.

    Only safe to ask below a heading: a document may attach numbering to its heading
    styles too, and `word_sections` asks the heading question first.
    """
    num_id = numbering_value(paragraph, "w:numId")
    if num_id is None or num_id == _NUMBERING_REMOVED:
        return frozenset()

    level = int(numbering_value(paragraph, "w:ilvl") or 0)
    if numbering_format(paragraph, num_id, level) == _BULLET_FORMAT:
        return frozenset({LIST})
    return frozenset({LIST, NUMBERED})


def numbering_value(paragraph: Paragraph, name: str) -> str | None:
    """One numbering property in effect on a paragraph: its own, else its style's.

    Direct formatting overrides a style property by property rather than wholesale,
    so a paragraph setting only its own w:ilvl still takes its w:numId from its style.
    """
    style = paragraph.style
    sources = (
        paragraph._p.find(qn("w:pPr")),
        style.element.find(qn("w:pPr")) if style is not None else None,
    )
    for properties in sources:
        if properties is None:
            continue
        element = properties.find(f"{qn('w:numPr')}/{qn(name)}")
        if element is not None and element.get(qn(_W_VAL)) is not None:
            return str(element.get(qn(_W_VAL)))
    return None


def numbering_format(paragraph: Paragraph, num_id: str, level: int) -> str:
    """How the named list marks its items at this depth.

    Where the list declares nothing at this level its first level decides, that being
    the only thing the document does say about it; where it declares nothing at all -
    no numbering part, no definition, no levels - the answer is a bullet, which is
    what a reader sees when Word has nothing else to draw.
    """
    formats = declared_formats(paragraph, num_id)
    if not formats:
        return _BULLET_FORMAT
    return formats.get(level, next(iter(formats.values())))


def declared_formats(paragraph: Paragraph, num_id: str) -> dict[int, str]:
    """The format the named list declares at each of its levels, as ilvl -> numFmt.

    A w:num is only a reference: the levels, and so the formats, live on the
    w:abstractNum it names.
    """
    numbering = numbering_element(paragraph)
    if numbering is None:
        return {}

    wanted = {
        value
        for num in numbering.findall(qn("w:num"))
        if num.get(qn("w:numId")) == num_id
        for child in num.findall(qn("w:abstractNumId"))
        if (value := child.get(qn(_W_VAL))) is not None
    }
    return {
        int(ilvl): value
        for definition in numbering.findall(qn("w:abstractNum"))
        if definition.get(qn("w:abstractNumId")) in wanted
        for level in definition.findall(qn("w:lvl"))
        if (ilvl := level.get(qn("w:ilvl"))) is not None
        for child in level.findall(qn("w:numFmt"))
        if (value := child.get(qn(_W_VAL))) is not None
    }


def numbering_element(paragraph: Paragraph) -> Any:
    """The document's numbering definitions, or None where it declares none.

    Reached through the relationships rather than through python-docx's own
    `numbering_part`, which raises NotImplementedError rather than saying the part
    is absent - and a document with no list at all has no such part.
    """
    for relationship in paragraph.part.rels.values():
        if relationship.reltype == RELATIONSHIP_TYPE.NUMBERING:
            return relationship.target_part.element
    return None


def cell_paragraphs(table: Table) -> Iterator[Paragraph]:
    """Every paragraph inside a table, in document order, each cell visited once.

    Word writes every cell exactly once however it is merged, so the cell elements
    answer directly the question that de-duplicating a grid can only approximate:
    `table.rows` hands back a merged cell once per grid position it spans, and text
    the document prints once would otherwise be scored as several.

    Do not put the grid walk back behind a set of `id(cell._tc)`. Those proxies are
    built on demand and released immediately, so CPython reuses their addresses and
    a cell never seen before collides with a freed one and is skipped -- silently,
    and differently on each run.
    """
    for cell in table._tbl.iter(qn("w:tc")):
        for paragraph in cell.findall(qn("w:p")):
            yield Paragraph(paragraph, table)


def is_callout(table: Table) -> bool:
    """Whether this table is a box Word drew rather than a grid of data.

    Word has no callout of its own, so a box is drawn either as a one-cell table or
    with the drawing tools, and it means the same thing by both. A reader sees a box
    either way, which is why both count as BOX rather than as a table of one cell.
    """
    rows = table._tbl.findall(qn("w:tr"))
    return len(rows) == 1 and len(rows[0].findall(qn("w:tc"))) == 1


def style_name(paragraph: Paragraph) -> str:
    """The paragraph's style name, lowercased, or "" when it has none."""
    style = paragraph.style
    if style is None:
        return ""
    name: str | None = style.name
    return name.lower() if name else ""


def is_heading(paragraph: Paragraph) -> bool:
    """Whether this paragraph opens a body section."""
    return style_name(paragraph).startswith(_BODY_STYLES)


def is_contents(paragraph: Paragraph) -> bool:
    """Whether this paragraph belongs to the table of contents rather than the body."""
    if style_name(paragraph).startswith(_CONTENTS_STYLES):
        return True
    return is_heading(paragraph) and normalise_heading(paragraph.text) in (
        _CONTENTS_HEADINGS
    )


def absorb(
    section: Section, paragraph: Paragraph, features: frozenset[str] = frozenset()
) -> None:
    """Add one paragraph's words, link targets and marks to a section.

    `features` is what the block around the paragraph already says about it, which
    only its container knows: the table or the box it sits in. What the paragraph
    itself says - the list it is an item of, the marks its runs wear - is read here.
    """
    section.bag.add_text(rendered_text(paragraph))
    for url in hyperlink_urls(paragraph):
        section.bag.add_url(url)
    mark_paragraph(section.bag, paragraph, features | list_features(paragraph))


def word_sections(document: docx.document.Document) -> list[Section]:
    """Split the document's body into sections of counted symbols.

    Everything before the first body heading is the cover page, and everything
    styled as a contents entry is navigation; neither is content the conversion is
    meant to carry, so both are dropped rather than scored. Dropped, and no more:
    a contents entry ends the section it sits in only when it is a heading, so that
    a stray contents style on a body paragraph costs that paragraph and not the rest
    of its section. A heading inside a table cell is table content, not a new
    section.
    """
    sections: list[Section] = []
    current: Section | None = None

    for block in iter_blocks(document):
        if isinstance(block, Table):
            if current is not None:
                features = frozenset({BOX if is_callout(block) else TABLE})
                for paragraph in cell_paragraphs(block):
                    absorb(current, paragraph, features)
            continue

        if is_contents(block):
            # Navigation rather than content, so it is never absorbed - but only a
            # contents *heading* ends the section it interrupts. A body paragraph
            # merely carrying a contents style, which one guide does on an empty one
            # mid-section, would otherwise take every paragraph after it up to the
            # next heading with it: words the page really shows, absent from this
            # side alone, so that nothing reads as missing and the marks on them are
            # charged to the parser as marks it invented. Across the guides no real
            # contents entry ever falls inside an open section - they sit ahead of
            # the first heading, where there is nothing to close - so closing one
            # here was only ever the destructive half of the rule.
            if is_heading(block):
                current = None
            continue

        if is_heading(block):
            current = Section(heading=block.text.strip())
            current.bag.add_text(current.heading)
            sections.append(current)
            continue

        if current is not None:
            absorb(current, block)

    return sections


def markdown_sections(document: models.MarkdownDocument) -> list[Section]:
    """The parser's own sections, reduced to the same counted symbols.

    Scored from the rendered Markdown rather than from the section objects, because
    the rendered Markdown is what is actually stored and read back.
    """
    sections = []
    for parsed in document.sections:
        section = Section(heading=parsed.heading)
        section.bag |= markdown_bag(parsed.markdown())
        sections.append(section)
    return sections


def markdown_bag(markdown: str) -> Bag:
    """Reduce Markdown to the symbols it renders as: its words, URLs and marks.

    Both sides of the audit have to be counted as a reader sees them, so this side is
    rendered rather than read. The markup is *followed* rather than deleted, which is
    what lets a mark be scored at all: `**` does not merely have to disappear, it has
    to say that the words between it are bold.

    Following it also settles by construction an ordering that deleting got wrong
    twice over. A tag is consumed as markup while the text around it is still
    encoded, so the entities in what is left are decoded afterwards and
    "&lt;Name and date&gt;" cannot be eaten as a tag on the way past - which is the
    same trap the parser carries in the other direction, where "&" has to be encoded
    first.

    Markdown's own markers still need no stripping once they are read: `#`, `*`, `|`,
    the backslash of an escape and the rest are punctuation, which the word pattern
    never matches.
    """
    bag = Bag()
    scan_blocks(bag, markdown.split("\n"), frozenset())
    return bag


def scan_blocks(bag: Bag, lines: list[str], features: frozenset[str]) -> None:
    """Fold a run of Markdown lines into the bag, under the features already in effect.

    Block markup is read line by line, because that is how Markdown writes it: a
    quote marker, a list marker or a row of pipes claims a whole line, and what is
    left of the line after its marker is inline content.

    A quote is the one block that holds blocks of its own - a callout can hold a list
    - so it is stripped of its markers and read again as lines. Nothing else nests,
    both real guides having no table inside a cell and no box inside a box.
    """
    item: frozenset[str] = frozenset()
    index = 0

    while index < len(lines):
        line = lines[index]

        end = table_end(lines, index)
        if end > index:
            scan_table(bag, lines[index:end], features)
            index, item = end, frozenset()
            continue

        if _QUOTE_MARKER.match(line):
            end = index
            while end < len(lines) and _QUOTE_MARKER.match(lines[end]):
                end += 1
            quoted = [
                _QUOTE_MARKER.sub("", quote, count=1) for quote in lines[index:end]
            ]
            scan_blocks(bag, quoted, features | {BOX})
            index, item = end, frozenset()
            continue

        index += 1

        heading = _ATX_HEADING.match(line)
        if heading:
            # A heading is scored as the words of a section, not as formatting: the
            # parser derives its own depth, so there is no level here to compare.
            bag.add_text(line[heading.end() :])
            item = frozenset()
            continue

        marker = list_marker(line)
        if marker is not None:
            item = features | marker[0]
            scan_line(bag, line[marker[1] :], item)
            continue

        # A line hanging under an item is the rest of that item: the parser indents
        # an item's later lines to its own text column, and there they still wear
        # whatever the item wears.
        if item and line.startswith(" "):
            scan_line(bag, line, item)
            continue

        item = frozenset()
        if line.strip():
            scan_line(bag, line, features)


def scan_table(bag: Bag, rows: list[str], features: frozenset[str]) -> None:
    """Fold one pipe table into the bag, cell by cell.

    The delimiter row says the rows around it are a table and prints nothing itself.

    A cell is scanned as one stretch of inline content, and there is no block markup
    inside it to look for. `tables` joins a cell's blocks with <br> because a pipe row
    cannot hold a newline, and what that produces is text: a hyphen at the start of a
    <br> piece is a hyphen, not a bullet, because GFM has no list inside a cell.

    Reading those hyphens as a list is a mistake the audit made until it was measured
    against the editor - which reads the cell as GFM says to, finds no list, and was
    then blamed for discarding one that was never written. An instrument must read
    the Markdown the parser produced, never the intent behind it: a list inside a
    table is lost at the conversion, and this side has to say so. The <br> itself
    still separates the words either side of it - `scan_inline` sees to that.
    """
    for row in rows:
        if _DELIMITER_ROW.match(row):
            continue
        for cell in row_cells(row):
            scan_line(bag, cell, features | {TABLE})


def row_cells(row: str) -> list[str]:
    """A pipe row's cells, split on the pipes that end a cell and no others.

    A pipe the document means as text is written escaped, so the backslash before it
    is what says this one is content rather than a column boundary.
    """
    cells: list[str] = []
    cell: list[str] = []
    line = row.strip()
    index = 0

    while index < len(line):
        character = line[index]
        if character == "\\" and index + 1 < len(line):
            cell.append(line[index : index + 2])
            index += 2
            continue
        if character == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(character)
        index += 1

    # The first pipe opens the row rather than ending a cell, so what it closed is
    # the empty stretch before it.
    return cells[1:]


def table_end(lines: list[str], start: int) -> int:
    """Where the table opening at `start` ends, or `start` where none opens there.

    A row alone is not a table: a paragraph can begin with a pipe, and only the
    delimiter under the first row makes what follows one. Requiring it keeps this
    off prose.
    """
    if not _PIPE_ROW.match(lines[start]):
        return start
    if start + 1 >= len(lines) or not _DELIMITER_ROW.match(lines[start + 1]):
        return start

    end = start + 2
    while end < len(lines) and _PIPE_ROW.match(lines[end]):
        end += 1
    return end


def list_marker(line: str) -> tuple[frozenset[str], int] | None:
    """The list this line is an item of and where its text starts, or None."""
    ordered = _ORDERED_ITEM.match(line)
    if ordered:
        return frozenset({LIST, NUMBERED}), ordered.end()

    bullet = _BULLET_ITEM.match(line)
    if bullet:
        return frozenset({LIST}), bullet.end()
    return None


@dataclass(frozen=True)
class Span:
    """A stretch of Markdown that markup claims, and what the markup says about it."""

    inner: str
    features: frozenset[str]
    end: int
    url: str = ""
    image: bool = False


def scan_line(bag: Bag, text: str, features: frozenset[str]) -> None:
    """Fold one line of inline Markdown into the bag.

    Words are counted from the whole line and marks from the stretches of it wearing
    the same marks, which is exactly the split the Word side makes: `absorb` hands
    `add_text` a whole paragraph, and `mark_paragraph` walks its runs separately.

    It has to be that way round on both sides. Word starts a run wherever anything
    changes, mid-word included, and the parser reproduces the break faithfully as
    markup - a bold run "Sending an emai" beside a plain "l." is written
    `**Sending an emai**l.`, which prints the whole word. Counting words per stretch
    instead reads that as "emai" and "l" here and "email" over there, and scores a
    word the page plainly says as lost.
    """
    segments: list[tuple[frozenset[str], str]] = []
    scan_inline(bag, segments, text, features)

    bag.add_text("".join(rendered for _, rendered in segments))
    for marks, group in groupby(segments, key=lambda segment: segment[0]):
        bag.add_marks("".join(rendered for _, rendered in group), marks)


def scan_inline(
    bag: Bag,
    segments: list[tuple[frozenset[str], str]],
    text: str,
    features: frozenset[str],
) -> None:
    """Split one line into the stretches its markup marks up, following that markup.

    Text accumulates until markup interrupts it, and what the markup claims is
    scanned again with its own mark added - so a mark inside a coloured span is still
    a mark. URLs and pictures go straight to the bag, having no words to belong to.

    A `<br>` is a line break and separates the words either side of it, which is
    exactly what `element_text` does with a w:br, so the two sides agree by
    construction. Every other tag closes up, which is the point of reading them at
    all: Word prints "16th" as one word however the "th" is drawn, and reading
    `16<sup>th</sup>` as written yields "16" and "th" and matches neither.
    """
    plain: list[str] = []
    index = 0

    def flush() -> None:
        if plain:
            segments.append((features, html.unescape("".join(plain))))
            plain.clear()

    while index < len(text):
        character = text[index]

        if character == "\\" and index + 1 < len(text):
            plain.append(text[index + 1])
            index += 2
            continue

        line_break = _LINE_BREAK_TAG.match(text, index)
        if line_break:
            plain.append(" ")
            index = line_break.end()
            continue

        span = span_at(text, index)
        if span is not None:
            flush()
            if span.url:
                bag.add_url(span.url)
            if span.image:
                bag.add_image()
            scan_inline(bag, segments, span.inner, features | span.features)
            index = span.end
            continue

        plain.append(character)
        index += 1

    flush()


def span_at(text: str, index: int) -> Span | None:
    """The span the markup at `index` opens, or None where nothing opens there.

    A marker with no partner is not markup: an unmatched asterisk is an asterisk, and
    returning None leaves it to be counted as the punctuation it is.
    """
    return emphasis_at(text, index) or tag_at(text, index) or bracketed_at(text, index)


def emphasis_at(text: str, index: int) -> Span | None:
    """The emphasis a run of markers opens, or None.

    How long the run is says which marks it carries, so `***` is one span wearing
    both rather than a bold beside an italic - and the close has to be a run of the
    same length, or `***a***` would close its bold on the first two of three.

    A run of a length nothing opens - the `*****` where a bold-italic span abuts a
    bold one - opens nothing, and its asterisks are counted as the punctuation they
    are. That is not a reading fault to fix: CommonMark cannot tell those two spans
    apart either, so the mark really is lost, and the editor would lose it too.
    """
    character = text[index]
    length = run_length(text, index, character)
    marks = _EMPHASIS_MARKS.get((character, length))
    if marks is None:
        return None

    close = find_run(text, character, length, index + length)
    if close == -1:
        return None
    return Span(text[index + length : close], marks, close + length)


def run_length(text: str, index: int, character: str) -> int:
    """How many times `character` repeats from `index`."""
    end = index
    while end < len(text) and text[end] == character:
        end += 1
    return end - index


def find_run(text: str, character: str, length: int, start: int) -> int:
    """Where the next run of exactly `length` `character`s begins, or -1."""
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] != character:
            index += 1
            continue
        run = run_length(text, index, character)
        if run == length:
            return index
        index += run
    return -1


def tag_at(text: str, index: int) -> Span | None:
    """The span an inline tag opens, or None."""
    match = _OPEN_TAG.match(text, index)
    if match is None:
        return None

    name = match.group(1).lower()
    closing = f"</{name}>"
    close = text.find(closing, match.end())
    if close == -1:
        return None
    return Span(
        text[match.end() : close],
        frozenset({_TAG_MARKS[name]}),
        close + len(closing),
    )


def bracketed_at(text: str, index: int) -> Span | None:
    """The span a bracket opens - a link, an image or a colour - or None.

    An image's target is a generated filename rather than an address, so it is not
    counted as a URL: the Word side has no such name to match it with, and scoring it
    would be crediting the Markdown with something the document never said.
    """
    image = text.startswith("![", index)
    if not image and text[index] != "[":
        return None

    open_at = index + (2 if image else 1)
    close = closing_bracket(text, open_at)
    if close == -1:
        return None
    inner = text[open_at:close]

    destination = _DESTINATION.match(text, close + 1)
    if destination:
        if image:
            return Span(inner, frozenset(), destination.end(), image=True)
        target = destination.group(1) or destination.group(2) or ""
        return Span(inner, frozenset({LINK}), destination.end(), url=target)

    attribute = _SPAN_ATTRIBUTE.match(text, close + 1)
    if attribute:
        # A class outside the colour vocabulary is markup this audit has no name
        # for, so its text is still counted and nothing is claimed about its mark.
        name = attribute.group(1)
        marks = frozenset({name}) if name in _COLOURS else frozenset()
        return Span(inner, marks, attribute.end())

    return None


def closing_bracket(text: str, start: int) -> int:
    """Where the bracket open at `start` closes, or -1 where it never does."""
    depth = 0
    index = start

    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return -1


def total_bag(sections: list[Section]) -> Bag:
    """Every section's symbols in one bag."""
    combined = Bag()
    for section in sections:
        combined |= section.bag
    return combined


@dataclass
class Row:
    """One line of the report: a section, and how well it survived."""

    label: str
    words: Coverage | None
    urls: Coverage | None
    marks: Coverage | None

    @property
    def missing(self) -> bool:
        return self.words is None


def bags_by_key(sections: list[Section]) -> dict[str, Bag]:
    """The sections' bags, gathered under the heading each will be matched on."""
    by_key: dict[str, Bag] = {}
    for section in sections:
        by_key.setdefault(section.key, Bag())
        by_key[section.key] |= section.bag
    return by_key


def build_rows(source: list[Section], rendered: list[Section]) -> list[Row]:
    """Pair the two sides up by heading and score each pairing.

    Matching on the heading rather than on position means a section the parser drops
    shows up as the one that is missing, instead of shifting every section after it
    into the wrong partner and reporting the whole document as lost.
    """
    by_key = bags_by_key(rendered)

    rows = []
    for section in source:
        match = by_key.get(section.key)
        if match is None:
            rows.append(Row(label=section.heading, words=None, urls=None, marks=None))
            continue
        rows.append(
            Row(
                label=section.heading,
                words=cover(section.bag.words, match.words),
                urls=cover(section.bag.urls, match.urls),
                marks=cover(section.bag.marks, match.marks),
            )
        )
    return rows


def percentage(coverage: Coverage | None) -> str:
    """A coverage as a column entry, with nothing to cover shown as a dash.

    Only a whole coverage is allowed to print 100%. Rounding one symbol short of
    complete up to 100% is how a real loss hides: 883 of 884 words is 99.89%, which
    the format would otherwise round to a column that says nothing is wrong. The
    same rounding at the bottom would report a section that converted nothing at all
    as 0%, which is what it is, so only the top needs the guard.
    """
    if coverage is None or coverage.fraction is None:
        return "-"
    if coverage.covered < coverage.total:
        return f"{min(coverage.fraction * 100, 99):.0f}%"
    return "100%"


def count(coverage: Coverage | None) -> str:
    return "-" if coverage is None or not coverage.total else f"{coverage.total:,}"


def format_row(
    label: str, words: Coverage | None, urls: Coverage | None, marks: Coverage | None
) -> str:
    return (
        f"  {label[:_LABEL_WIDTH]:<{_LABEL_WIDTH}}"
        f"{count(words):>7}{percentage(words):>9}"
        f"{count(urls):>7}{percentage(urls):>9}"
        f"{count(marks):>7}{percentage(marks):>9}"
    )


def report(
    name: str,
    source: list[Section],
    rendered: list[Section],
    whole: Bag,
    editor: Bag | None,
    show_missing: bool,
    top: int,
) -> None:
    """Print the section-by-section report for one document.

    `editor` is the same document once more, after a trip through the guidance
    editor's schema, or None where that leg was not asked for. It is scored against
    the Word side like the parser's Markdown is, so the two read as what they are:
    two legs of one pipeline, losing marks for entirely different reasons.
    """
    print(f"\n{name}\n")

    if not source:
        print("  No body headings found: nothing to audit but the cover and contents.")
        return

    rows = build_rows(source, rendered)
    rule = f"  {'-' * (_LABEL_WIDTH + _COLUMNS_WIDTH)}"

    print(
        f"  {'section':<{_LABEL_WIDTH}}{'words':>7}{'covered':>9}"
        f"{'urls':>7}{'covered':>9}{'marks':>7}{'covered':>9}"
    )
    print(rule)

    for row in rows:
        if row.missing:
            print(
                f"  {row.label[:_LABEL_WIDTH]:<{_LABEL_WIDTH}}"
                f"{'MISSING':>{_COLUMNS_WIDTH}}"
            )
        else:
            print(format_row(row.label, row.words, row.urls, row.marks))

    source_total = total_bag(source)
    print(rule)
    print(
        format_row(
            "matched sections",
            sum_coverage(rows, "words"),
            sum_coverage(rows, "urls"),
            sum_coverage(rows, "marks"),
        )
    )
    print(
        format_row(
            "whole document",
            cover(source_total.words, whole.words),
            cover(source_total.urls, whole.urls),
            cover(source_total.marks, whole.marks),
        )
    )
    if editor is not None:
        # The same three measurements after the editor has had the document, which is
        # the state anything stored will actually be in: nobody reads the parser's
        # output, they read what came back from the first save.
        print(
            format_row(
                "after a TipTap save",
                cover(source_total.words, editor.words),
                cover(source_total.urls, editor.urls),
                cover(source_total.marks, editor.marks),
            )
        )

    missing_count = sum(1 for row in rows if row.missing)
    print(
        f"\n  {len(source)} sections in Word, "
        f"{len(source) - missing_count} matched, {missing_count} missing"
    )

    print_features(source_total, whole, editor)

    if show_missing:
        print_missing(source, rendered, top)
        if editor is not None:
            print_discarded(whole, editor, top)


def sum_coverage(rows: list[Row], attribute: str) -> Coverage | None:
    """Add the matched rows' coverages together into one."""
    parts = [
        getattr(row, attribute) for row in rows if getattr(row, attribute) is not None
    ]
    if not parts:
        return None
    return Coverage(
        total=sum(p.total for p in parts),
        covered=sum(p.covered for p in parts),
        extra=sum(p.extra for p in parts),
    )


def print_features(source: Bag, rendered: Bag, editor: Bag | None) -> None:
    """Break the whole document's marks down by the feature each one is.

    Scored over the document rather than per section, because a feature is a property
    of the conversion and not of a section: a lost underline is the same fault
    wherever it turns up, and one line per feature says which fault it is. The
    section table above already says where.

    "spurious" is the other half of the measurement, and the half a coverage score
    cannot show: marks the Markdown wears that the document does not. A parser that
    bolds a whole paragraph because one run in it was bold covers every bold word
    there was and still gets that paragraph wrong, and this is the column it shows up
    in.

    "kept" is the same measurement taken one leg further on, against the Markdown the
    editor hands back rather than the Markdown the parser wrote. It shares its "word"
    denominator with "covered" so the two read side by side, and the pair says which
    repository a repair belongs in: a feature at 100% covered and 0% kept was written
    correctly and then discarded by a schema that cannot model it.
    """
    scores = [
        (
            feature,
            cover(marks_of(source.marks, feature), marks_of(rendered.marks, feature)),
            kept_coverage(source, editor, feature),
        )
        for feature in _FEATURES
    ]
    used = [score for score in scores if score[1].total or score[1].extra]

    print("\n  Formatting features\n")
    if not used:
        print("  The document wears none of the marks the audit knows about.")
        return

    # An absent column is an empty string, not a zero-width one: a format width
    # narrower than what it is given pads nothing and truncates nothing, so a
    # zero-width "kept" would print in full and run into the column beside it.
    print(
        f"  {'feature':<{_FEATURE_WIDTH}}{'word':>8}{'covered':>9}"
        f"{kept_column('kept', editor)}{'spurious':>9}"
    )
    print(f"  {'-' * (_FEATURE_WIDTH + 25 + (0 if editor is None else _KEPT_WIDTH))}")
    for feature, score, kept in used:
        print(
            f"  {feature:<{_FEATURE_WIDTH}}{count(score):>8}{percentage(score):>9}"
            f"{kept_column(percentage(kept), editor)}{score.extra:>9,}"
        )


def kept_column(entry: str, editor: Bag | None) -> str:
    """One "kept" cell, or nothing at all where that leg was not asked for."""
    return "" if editor is None else f"{entry:>{_KEPT_WIDTH}}"


def kept_coverage(source: Bag, editor: Bag | None, feature: str) -> Coverage | None:
    """How much of one Word feature survives the editor as well as the parser."""
    if editor is None:
        return None
    return cover(marks_of(source.marks, feature), marks_of(editor.marks, feature))


def print_missing(source: list[Section], rendered: list[Section], top: int) -> None:
    """List the symbols that never reached the Markdown, section by section."""
    by_key = bags_by_key(rendered)

    print("\n  Unconverted symbols\n")

    lost = False
    for section in source:
        match = by_key.get(section.key, Bag())
        lost_words = section.bag.words - match.words
        lost_urls = section.bag.urls - match.urls
        lost_marks = section.bag.marks - match.marks
        if not lost_words and not lost_urls and not lost_marks:
            continue

        lost = True
        print(f"  {section.heading}")
        if lost_words:
            print(f"    {'words:':<{_MARK_LABEL_WIDTH}}{listed(lost_words, top)}")
        for url in sorted(lost_urls):
            print(f"    {'url:':<{_MARK_LABEL_WIDTH}}{section.bag.written(url)}")
        for feature in _FEATURES:
            words = marks_of(lost_marks, feature)
            if words:
                print(f"    {feature + ':':<{_MARK_LABEL_WIDTH}}{listed(words, top)}")
        print()

    # Said outright, because a bare heading reads as a report that stopped short.
    if not lost:
        print("  Nothing: every word, URL and mark reached the Markdown.\n")


def listed(words: Counter[str], top: int) -> str:
    """The commonest of a counter of words, as one line, saying what was left out.

    A picture has no word to name it, so the count stands in for one.
    """
    if list(words) == [_NO_WORD]:
        return f"{words[_NO_WORD]}, none of which has words to name it"

    shown = ", ".join(
        f"{word} x{n}" if n > 1 else word for word, n in words.most_common(top)
    )
    remainder = len(words) - min(top, len(words))
    return f"{shown}, ... and {remainder} more" if remainder else shown


def print_discarded(rendered: Bag, editor: Bag, top: int) -> None:
    """List what the editor threw away that the parser had got right.

    Scored against the parser's Markdown rather than against Word, because that is
    the question this block answers: not "what did the pipeline lose" - the feature
    table says that - but "what did the parser hand over that did not survive". A
    mark the parser never wrote cannot be discarded by anything downstream, and
    listing it here would send someone to the wrong repository.
    """
    print("\n  Discarded by the editor\n")

    lost_words = rendered.words - editor.words
    lost_urls = rendered.urls - editor.urls
    lost_marks = rendered.marks - editor.marks

    if lost_words:
        print(f"    {'words:':<{_MARK_LABEL_WIDTH}}{listed(lost_words, top)}")
    for url in sorted(lost_urls):
        print(f"    {'url:':<{_MARK_LABEL_WIDTH}}{rendered.written(url)}")
    for feature in _FEATURES:
        words = marks_of(lost_marks, feature)
        if words:
            print(f"    {feature + ':':<{_MARK_LABEL_WIDTH}}{listed(words, top)}")

    if not lost_words and not lost_urls and not lost_marks:
        print("  Nothing: the editor gives back everything the parser wrote.")
    print()


def parse_args() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    argument_parser.add_argument(
        "document", help="Path to the guidance document (.docx)."
    )
    argument_parser.add_argument(
        "--missing",
        action="store_true",
        help="List the words, URLs and marks that never reached the Markdown.",
    )
    argument_parser.add_argument(
        "--tiptap",
        metavar="FILE",
        help=(
            "Markdown from a TipTap load/save round trip of this document, to score "
            "as a third leg. Produced by the UI repository, so this is normally "
            "reached as `uv run task audit <document.docx> --tiptap`."
        ),
    )
    argument_parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_TOP,
        help=f"How many missing words to list per section (default: {_DEFAULT_TOP}).",
    )
    return argument_parser.parse_args()


def _read(path: str) -> str:
    """The text of a file given on the command line."""
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    args = parse_args()
    source_path = Path(args.document)

    try:
        raw = source_path.read_bytes()
        document = parser.parse_docx(raw)
    except (OSError, DocumentParseError) as error:
        print(f"{source_path.name}: {error}", file=sys.stderr)
        return 1

    try:
        editor = None if args.tiptap is None else markdown_bag(_read(args.tiptap))
    except OSError as error:
        print(f"{source_path.name}: {error}", file=sys.stderr)
        return 1

    word_side = word_sections(docx.Document(source_path))
    markdown_side = markdown_sections(document)

    # Scored against everything the parser produced, not only its sections, so a
    # document whose text all landed in one wrongly-named section still shows the
    # text as having survived somewhere.
    whole = markdown_bag(document.markdown())

    report(
        source_path.name,
        word_side,
        markdown_side,
        whole,
        editor,
        show_missing=args.missing,
        top=args.top,
    )

    # The score is a measurement, not a gate: only an unreadable document fails.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
