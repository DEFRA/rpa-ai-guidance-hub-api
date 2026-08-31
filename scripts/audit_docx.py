#!/usr/bin/env python3
"""Report what the guidance parser loses when it renders a .docx as Markdown.

Reads the Word document twice over: once directly, for the words and URLs Word
would put on the page, and once through ``parser.parse_docx`` for the Markdown the
API would really store. Each side is reduced to a counted bag of symbols per
section, and the report is the share of the Word bag the Markdown bag covers.

The cover page and the table of contents are excluded: the audit begins at the
first body heading, which is also where the parser's own sections begin.

Nothing but python-docx and the parser is involved -- no configuration, database,
S3 or Bedrock access -- so this runs against any document on disk.

Usage:
  uv run scripts/audit_docx.py <document.docx> [--missing] [--top N]

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import docx
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

# Markdown link and image syntax: the display text is rendered, the target is a URL.
# The target is wrapped in <> when it holds whitespace or a bracket and written bare
# otherwise. That is CommonMark, and it is also exactly what the parser's own
# `_destination` writes; reading only the bare form stopped one SharePoint address at
# the "(" in its filename and scored an address the Markdown states in full as lost.
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\((?:<([^>]+)>|([^)\s]+))[^)]*\)")

# The inline HTML the parser writes where Markdown has no marker of its own: <u>,
# <sup>, <sub>, <span style="...">, and <br> for a line break inside a table row.
# The tag name is letters and digits, so an angle-wrapped link destination -- which
# has a colon straight after its scheme -- is not mistaken for a tag.
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[a-z][a-z0-9]*(?:\s[^>]*)?/?>", re.IGNORECASE)

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
_DEFAULT_TOP = 12


@dataclass
class Bag:
    """The counted symbols of one section: its words and its URLs, kept apart.

    Separate counters because a document with many links should not have its prose
    score moved by them, in either direction.
    """

    words: Counter[str] = field(default_factory=Counter)
    urls: Counter[str] = field(default_factory=Counter)

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

    def written(self, key: str) -> str:
        """How the document wrote the URL that normalised to `key`."""
        return self.forms.get(key, key)

    def __ior__(self, other: Bag) -> Bag:
        self.words.update(other.words)
        self.urls.update(other.urls)
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


def cover(source: Counter[str], rendered: Counter[str]) -> Coverage:
    """Score how much of `source` `rendered` accounts for.

    The intersection is a multiset one, so repeats count: dropping three of five
    occurrences of a word scores that word at two fifths, and the Markdown earns
    nothing for words the document never said.
    """
    return Coverage(
        total=sum(source.values()),
        covered=sum((source & rendered).values()),
        extra=sum((rendered - source).values()),
    )


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


def absorb(section: Section, paragraph: Paragraph) -> None:
    """Add one paragraph's rendered words and link targets to a section."""
    section.bag.add_text(rendered_text(paragraph))
    for url in hyperlink_urls(paragraph):
        section.bag.add_url(url)


def word_sections(document: docx.document.Document) -> list[Section]:
    """Split the document's body into sections of counted symbols.

    Everything before the first body heading is the cover page, and everything
    styled as a contents entry is navigation; neither is content the conversion is
    meant to carry, so both are dropped rather than scored. A heading inside a table
    cell is table content, not a new section.
    """
    sections: list[Section] = []
    current: Section | None = None

    for block in iter_blocks(document):
        if isinstance(block, Table):
            if current is not None:
                for paragraph in cell_paragraphs(block):
                    absorb(current, paragraph)
            continue

        if is_contents(block):
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
    """Reduce Markdown to the symbols it renders as.

    Both sides of the audit have to be counted as a reader sees them, so this side
    is rendered rather than read. A link contributes both halves: its display text
    is words on the page and its target is a URL. Then the inline HTML resolves --
    see `rendered_markdown`.

    Markdown's own markers need no stripping: `#`, `*`, `|`, the backslash of an
    escape and the rest are punctuation, which the word pattern never matches. That
    was once the whole story, and it stopped being so when the parser began writing
    tags for the marks Markdown cannot spell.
    """
    bag = Bag()

    def unlink(match: re.Match[str]) -> str:
        bag.add_url(match.group(2) or match.group(3))
        return f" {match.group(1)} "

    bag.add_text(rendered_markdown(_MD_LINK.sub(unlink, markdown)))
    return bag


def rendered_markdown(markdown: str) -> str:
    """Markdown with its inline HTML resolved: tags gone, entities decoded.

    A `<br>` is a line break and separates the words either side of it, which is
    exactly what `element_text` does with a `w:br`, so the two sides agree by
    construction. Every other tag closes up, and that is the point of doing this at
    all: Word prints "16th" as one word however the "th" is drawn, and reading
    `16<sup>th</sup>` as written yields "16" and "th" and matches neither.

    The tags go *before* the entities are decoded, and the order is load-bearing:
    the other way round, "&lt;Name and date&gt;" decodes to "<Name and date>" and is
    then eaten as a tag, losing three words the document prints. It is the same trap
    the parser carries in the other direction, where "&" has to be encoded first.
    """
    return html.unescape(_HTML_TAG.sub("", _LINE_BREAK_TAG.sub(" ", markdown)))


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

    @property
    def missing(self) -> bool:
        return self.words is None


def build_rows(source: list[Section], rendered: list[Section]) -> list[Row]:
    """Pair the two sides up by heading and score each pairing.

    Matching on the heading rather than on position means a section the parser drops
    shows up as the one that is missing, instead of shifting every section after it
    into the wrong partner and reporting the whole document as lost.
    """
    by_key: dict[str, Bag] = {}
    for section in rendered:
        by_key.setdefault(section.key, Bag())
        by_key[section.key] |= section.bag

    rows = []
    for section in source:
        match = by_key.get(section.key)
        if match is None:
            rows.append(Row(label=section.heading, words=None, urls=None))
            continue
        rows.append(
            Row(
                label=section.heading,
                words=cover(section.bag.words, match.words),
                urls=cover(section.bag.urls, match.urls),
            )
        )
    return rows


def percentage(coverage: Coverage | None) -> str:
    """A coverage as a column entry, with nothing to cover shown as a dash."""
    if coverage is None or coverage.fraction is None:
        return "-"
    return f"{coverage.fraction * 100:.0f}%"


def count(coverage: Coverage | None) -> str:
    return "-" if coverage is None or not coverage.total else f"{coverage.total:,}"


def format_row(label: str, words: Coverage | None, urls: Coverage | None) -> str:
    return (
        f"  {label[:_LABEL_WIDTH]:<{_LABEL_WIDTH}}"
        f"{count(words):>7}{percentage(words):>9}"
        f"{count(urls):>7}{percentage(urls):>9}"
    )


def report(
    name: str,
    source: list[Section],
    rendered: list[Section],
    whole: Bag,
    show_missing: bool,
    top: int,
) -> None:
    """Print the section-by-section report for one document."""
    print(f"\n{name}\n")

    if not source:
        print("  No body headings found: nothing to audit but the cover and contents.")
        return

    rows = build_rows(source, rendered)
    rule = f"  {'-' * (_LABEL_WIDTH + 32)}"

    print(
        f"  {'section':<{_LABEL_WIDTH}}{'words':>7}{'covered':>9}"
        f"{'urls':>7}{'covered':>9}"
    )
    print(rule)

    for row in rows:
        if row.missing:
            print(f"  {row.label[:_LABEL_WIDTH]:<{_LABEL_WIDTH}}{'MISSING':>32}")
        else:
            print(format_row(row.label, row.words, row.urls))

    source_total = total_bag(source)
    print(rule)
    print(
        format_row(
            "matched sections",
            sum_coverage(rows, "words"),
            sum_coverage(rows, "urls"),
        )
    )
    print(
        format_row(
            "whole document",
            cover(source_total.words, whole.words),
            cover(source_total.urls, whole.urls),
        )
    )

    missing_count = sum(1 for row in rows if row.missing)
    print(
        f"\n  {len(source)} sections in Word, "
        f"{len(source) - missing_count} matched, {missing_count} missing"
    )

    if show_missing:
        print_missing(source, rendered, top)


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


def print_missing(source: list[Section], rendered: list[Section], top: int) -> None:
    """List the symbols that never reached the Markdown, section by section."""
    by_key: dict[str, Bag] = {}
    for section in rendered:
        by_key.setdefault(section.key, Bag())
        by_key[section.key] |= section.bag

    print("\n  Unconverted symbols\n")
    for section in source:
        match = by_key.get(section.key, Bag())
        lost_words = section.bag.words - match.words
        lost_urls = section.bag.urls - match.urls
        if not lost_words and not lost_urls:
            continue

        print(f"  {section.heading}")
        if lost_words:
            shown = ", ".join(
                f"{word} x{n}" if n > 1 else word
                for word, n in lost_words.most_common(top)
            )
            remainder = len(lost_words) - min(top, len(lost_words))
            suffix = f", ... and {remainder} more" if remainder else ""
            print(f"    words: {shown}{suffix}")
        for url in sorted(lost_urls):
            print(f"    url:   {section.bag.written(url)}")
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
        help="List the words and URLs that never reached the Markdown.",
    )
    argument_parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_TOP,
        help=f"How many missing words to list per section (default: {_DEFAULT_TOP}).",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.document)

    try:
        raw = source_path.read_bytes()
        document = parser.parse_docx(raw)
    except (OSError, DocumentParseError) as error:
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
        show_missing=args.missing,
        top=args.top,
    )

    # The score is a measurement, not a gate: only an unreadable document fails.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
