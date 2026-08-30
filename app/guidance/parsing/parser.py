"""Read a Word (.docx) package into a MarkdownDocument."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.guidance.parsing import inline, models
from app.guidance.parsing.errors import DocumentParseError
from app.guidance.parsing.ooxml import is_toggle_on

if TYPE_CHECKING:
    from collections.abc import Iterator

    import docx.document

# Styles that open the document's navigation, so the cover page has ended.
_CONTENTS_STYLE_PREFIXES = ("toc", "contents", "table of contents")

# Word's own annex style is not a "Heading n", so it carries no level and has to be
# matched by name - see the appendix feature.
_APPENDIX_STYLE_PREFIX = "appendix"

# Word numbers its heading styles from 1, and the level is the whole of the name
# after the word: "Heading 2 Box" is a style in its own right, not a Heading 2.
_HEADING_STYLE = re.compile(r"heading\s*([1-9]\d*)$")

_TITLE_STYLE = "title"

# Between the author's distinct title parts, and within one hand-wrapped part.
_TITLE_SEPARATOR = " — "
_WRAPPED_LINE_SEPARATOR = " "


def parse_docx(source: bytes) -> models.MarkdownDocument:
    """Parse the bytes of a .docx file into a MarkdownDocument.

    Raises:
        DocumentParseError: if the bytes are not a readable Word document.
    """
    document = _open(source)
    return models.MarkdownDocument(
        title=_extract_title(document),
        sections=_extract_sections(document),
    )


def _open(source: bytes) -> docx.document.Document:
    """Open .docx bytes, mapping every way that can fail to one error.

    python-docx raises a different exception for each kind of bad input, and
    only ever raises PackageNotFoundError for a *path* - never for the stream we
    hand it. The ValueError it raises for a non-Word package also interpolates the
    stream's repr into its message, which is meaningless to a caller, so the text
    is replaced here and the original kept as the cause.
    """
    try:
        return docx.Document(io.BytesIO(source))
    except zipfile.BadZipFile as exc:
        msg = "Source is not a .docx file: it is not a zip archive."
        raise DocumentParseError(msg) from exc
    except KeyError as exc:
        msg = "Source is not a .docx file: the zip archive is not an Office package."
        raise DocumentParseError(msg) from exc
    except (ValueError, PackageNotFoundError) as exc:
        msg = "Source is not a Word document."
        raise DocumentParseError(msg) from exc


def _extract_title(document: docx.document.Document) -> str:
    """Return the document's title, preferring the one printed on the page.

    The cover is read first and the document properties are only a fallback. Core
    properties are metadata that Word carries forward from whatever the file was
    copied from, so they go stale silently while the cover is what a reader sees:
    one real guide carried a "2024" title in its properties, printed no year on its
    cover, and was the 2026 edition.
    """
    printed = _cover_title(document)
    if printed:
        return printed

    stored = document.core_properties.title
    return stored.strip() if stored else ""


def _cover_title(document: docx.document.Document) -> str:
    """Reconstruct the title printed on the cover page.

    Where the cover marks its title with the Title style, only those paragraphs are
    taken; otherwise the whole cover is. Each part is put back together as the author
    wrapped it, and the parts are joined with a dash. The text is joined but not
    otherwise tidied - a defect in a title is a finding to report, not noise to
    normalise away.
    """
    groups = _cover_groups(document)

    marked = [[p for p in group if _style_name(p) == _TITLE_STYLE] for group in groups]
    if any(marked):
        groups = [group for group in marked if group]

    return _TITLE_SEPARATOR.join(
        _WRAPPED_LINE_SEPARATOR.join(paragraph.text.strip() for paragraph in group)
        for group in groups
    )


def _cover_groups(document: docx.document.Document) -> list[list[Paragraph]]:
    """Return the cover page's paragraphs, grouped as the author laid them out.

    A blank paragraph separates one part of the title from the next; consecutive
    paragraphs are one part that the author wrapped by hand because it was too long
    for the line. Runs of blanks - including the ones padding the top of the page -
    separate but never form a part of their own. Headers and footers live in a
    separate XML part and so are excluded automatically.

    The cover ends at the first table of contents, body heading or page break. The
    two spellings of a page break stop it at different points, because they mean
    opposite things about the paragraph carrying them.
    """
    groups: list[list[Paragraph]] = []
    part: list[Paragraph] = []

    for paragraph in document.paragraphs:
        # This paragraph is already on the page after the cover, so its text is not
        # part of the title.
        if _opens_body(paragraph) or _starts_new_page(paragraph):
            break

        if paragraph.text.strip():
            part.append(paragraph)
        elif part:
            groups.append(part)
            part = []

        # ...whereas a break within the paragraph ends the page after it, so what it
        # says still belongs to the cover.
        if _ends_page(paragraph):
            break

    if part:
        groups.append(part)

    return groups


def _opens_body(paragraph: Paragraph) -> bool:
    """Whether this paragraph opens the navigation or the body, ending the cover.

    The heading check is what stops a document with no cover page break at all from
    swallowing its opening heading into the title. It asks the same question that
    opens a section, so the two rules cannot drift apart.
    """
    return (
        _is_contents(paragraph)
        or _style_name(paragraph).startswith(_APPENDIX_STYLE_PREFIX)
        or _heading_level(paragraph) is not None
    )


def _is_contents(paragraph: Paragraph) -> bool:
    """Whether this paragraph is one of the document's contents entries."""
    return _style_name(paragraph).startswith(_CONTENTS_STYLE_PREFIXES)


def _starts_new_page(paragraph: Paragraph) -> bool:
    """Whether this paragraph is forced to the top of a new page by its properties."""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return False
    return is_toggle_on(properties.find(qn("w:pageBreakBefore")))


def _ends_page(paragraph: Paragraph) -> bool:
    """Whether this paragraph carries an explicit page break in its own text."""
    return any(
        break_element.get(qn("w:type")) == "page"
        for run in paragraph.runs
        for break_element in run._r.findall(qn("w:br"))
    )


@dataclass
class _OpenSection:
    """A section still open for children as the walk moves down the document.

    `children` is how many sections have been opened directly beneath this one, and
    so is the ordinal the next one takes. The stack's first frame stands for the
    document itself, holding no section, so a top-level heading is counted and
    parented by the same code as any other.
    """

    level: int = 0
    section: models.MarkdownSection | None = None
    children: int = 0


def _extract_sections(document: docx.document.Document) -> list[models.MarkdownSection]:
    """Turn the document's headings into a flat list of sections in document order.

    Word does not put the number in the text: both real guides attach the numbering
    to the heading *styles*, so Word generates "4.3.1.1" when it renders the page and
    the paragraph itself says only "Split". The number is therefore derived here,
    from nothing but each heading's level relative to the one before it.

    Levels are read as relative, never absolute: a document that opens at Heading 2
    still starts at 1, and a heading that skips a level nests one deep rather than
    leaving a gap in the number.

    Every other paragraph is content, and belongs to the section opened most recently
    whatever its depth. Anything ahead of the first heading is not: in both real
    guides everything there is the cover page and the contents - 25 and 30 blocks of
    it, and not one line of body prose - and a contents page is regenerated from the
    headings anyway.
    """
    sections: list[models.MarkdownSection] = []
    # The document's own frame is never popped: it sits at level 0, and a heading's
    # level is never lower than 1.
    stack = [_OpenSection()]

    for paragraph in _body_paragraphs(document):
        level = _heading_level(paragraph)
        heading = paragraph.text.strip()

        # A heading with nothing in it is a layout artefact - numbering it would put
        # a section in the output that the document does not have.
        if level is None or not heading:
            _collect_content(sections, paragraph)
            continue

        while stack[-1].level >= level:
            stack.pop()

        parent = stack[-1]
        parent.children += 1
        section = models.MarkdownSection(
            heading=heading,
            ordinal=parent.children,
            parent=parent.section,
        )
        sections.append(section)
        stack.append(_OpenSection(level=level, section=section))

    return sections


def _body_paragraphs(document: docx.document.Document) -> Iterator[Paragraph]:
    """The body's paragraphs, in document order.

    `document.paragraphs` returns the same paragraphs today and would go on doing so.
    The walk is written out here because the body holds the document's tables too,
    and the paragraphs inside a table are reachable from nowhere else.
    """
    for element in document.element.body:
        if element.tag == qn("w:p"):
            yield Paragraph(element, document)


def _collect_content(
    sections: list[models.MarkdownSection], paragraph: Paragraph
) -> None:
    """File a body paragraph under the open section, where it says anything.

    Contents entries are left out wherever they turn up. They sit ahead of every
    heading in both real guides and so never reach here, but a document whose
    contents page opens with a heading of its own would otherwise file its whole
    table of contents as that section's prose.
    """
    if not sections or _is_contents(paragraph):
        return

    block = inline.paragraph_markdown(paragraph)
    if not block:
        return

    section = sections[-1]
    section.content = f"{section.content}\n\n{block}" if section.content else block


def _heading_level(paragraph: Paragraph) -> int | None:
    """The level of a "Heading n" paragraph, or None if it is not one.

    The level has to come from the style name because that is the only place it is
    written: the outline level a document declares for a heading lives on the style
    definition, not on the paragraph. A custom style whose name only begins like a
    heading - "Heading Box", "Heading 2 Box" - names no level of its own and is
    therefore body text.
    """
    match = _HEADING_STYLE.match(_style_name(paragraph))
    return int(match[1]) if match else None


def _style_name(paragraph: Paragraph) -> str:
    """The paragraph's style name, lowercased, or "" when it has none.

    The two guards are not alike. python-docx annotates `style` as optional but
    resolves an unset or unknown style id to the document's default, so it never
    actually returns None - that guard is there for the type checker, and writing
    it as an expression keeps an unreachable branch out of the coverage report.
    `name` really can be None, for a style carrying no w:name element, which is
    covered by a test.
    """
    style = paragraph.style
    name = style.name if style is not None else None
    return name.lower() if name else ""
