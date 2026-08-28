"""Read a Word (.docx) package into a ParsedDocument."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING, Any

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn

from app.guidance.parsing import models
from app.guidance.parsing.errors import DocumentParseError

if TYPE_CHECKING:
    import docx.document
    from docx.text.paragraph import Paragraph

_W_VAL = "w:val"

# Styles that open the document's navigation, so the cover page has ended.
_CONTENTS_STYLE_PREFIXES = ("toc", "contents", "table of contents")

# Styles that open the body, so the cover page has ended. "Appendix" is included
# because Word's own annex style is not a "Heading n" - see the heading feature.
_BODY_STYLE_PREFIXES = ("heading", "appendix")

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
    return models.MarkdownDocument(title=_extract_title(document))


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
    swallowing its opening heading into the title.
    """
    style = _style_name(paragraph)
    return style.startswith(_CONTENTS_STYLE_PREFIXES) or style.startswith(
        _BODY_STYLE_PREFIXES
    )


def _starts_new_page(paragraph: Paragraph) -> bool:
    """Whether this paragraph is forced to the top of a new page by its properties."""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return False
    return _is_toggle_on(properties.find(qn("w:pageBreakBefore")))


def _ends_page(paragraph: Paragraph) -> bool:
    """Whether this paragraph carries an explicit page break in its own text."""
    return any(
        break_element.get(qn("w:type")) == "page"
        for run in paragraph.runs
        for break_element in run._r.findall(qn("w:br"))
    )


def _is_toggle_on(element: Any) -> bool:
    """Whether an OOXML boolean toggle is present and not explicitly disabled.

    Toggle properties are 'on' by their presence alone; they are turned off with
    w:val="false" or w:val="0".
    """
    if element is None:
        return False
    return element.get(qn(_W_VAL)) not in ("false", "0")


def _style_name(paragraph: Paragraph) -> str:
    """The paragraph's style name, lowercased, or "" when it has none."""
    style = paragraph.style
    if style is None:
        return ""
    name: str | None = style.name
    return name.lower() if name else ""
