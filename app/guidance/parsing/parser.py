"""Read a Word (.docx) package into a MarkdownDocument."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.guidance.parsing import (
    borders,
    images,
    inline,
    lists,
    models,
    tables,
    textboxes,
)
from app.guidance.parsing.errors import DocumentParseError
from app.guidance.parsing.models import MinimalDocumentInfo
from app.guidance.parsing.ooxml import W_PPR, is_toggle_on

if TYPE_CHECKING:
    from collections.abc import Iterator

    import docx.document

# Styles that open the document's navigation, so the cover page has ended.
_CONTENTS_STYLE_PREFIXES = ("toc", "contents", "table of contents")

# Word's own annex styles are not "Heading n", so they carry no level and have to be
# matched by name. Neither guide spells it "annex" or "schedule", but a template that
# did would mean exactly the same thing.
_APPENDIX_STYLE_PREFIXES = ("appendix", "annex", "schedule")

# An appendix is a top-level section. CS's contents pulls the style in with
# TOC \o "1-4" \h \z \t "Appendix,1" - the document declaring the level itself.
_APPENDIX_LEVEL = 1

# Word numbers its heading styles from 1, and the level is the whole of the name
# after the word: "Heading 2 Box" is a style in its own right, not a Heading 2.
_HEADING_STYLE = re.compile(r"heading\s*([1-9]\d*)$")

# The blocks of a document body. Anything else there - a bookmark, a section break,
# a proofing mark - says nothing the output carries.
_BODY_BLOCKS = (qn("w:p"), qn("w:tbl"))

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
    sections, bookmarks = _extract_sections(document)
    # Naming comes after the walk because only a finished section can say what its
    # pictures should be called, and a picture in a block that never reached a
    # section is dropped with the block rather than extracted and thrown away.
    images.name_all(sections, document.part)
    return models.MarkdownDocument(
        title=_extract_title(document),
        sections=sections,
        bookmarks=bookmarks,
    )


def parse_minimal(source: bytes) -> MinimalDocumentInfo:
    """The title, version and last-modified date `source` carries, if any.

    Reuses the same cover-page title heuristic a full parse uses, so the two never
    disagree about what a document is called. Version and last-modified come
    straight off the package's own core properties - `cp:version` and
    `dcterms:modified` - because unlike the title, Word gives an author nowhere
    else to put either one.

    Raises:
        DocumentParseError: if `source` cannot be opened as a Word document at
            all. A field the document simply never set is not this - it is
            reported back as empty/None, not raised.
    """
    document = _open(source)
    core_properties = document.core_properties

    return MinimalDocumentInfo(
        title=_extract_title(document),
        version=(core_properties.version or "").strip(),
        last_modified=core_properties.modified,
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
    copied from, so they go stale silently - a properties title can name a year the
    document was superseded from - while the cover is what a reader sees.
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
        or _is_appendix(paragraph)
        or _heading_level(paragraph) is not None
    )


def _is_contents(paragraph: Paragraph) -> bool:
    """Whether this paragraph is one of the document's contents entries."""
    return _style_name(paragraph).startswith(_CONTENTS_STYLE_PREFIXES)


def _starts_new_page(paragraph: Paragraph) -> bool:
    """Whether this paragraph is forced to the top of a new page by its properties."""
    properties = paragraph._p.find(qn(W_PPR))
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

    `numbered` and `appendices` are how many of each kind have been opened directly
    beneath this one, and so are the ordinal the next one of that kind takes. The
    stack's first frame stands for the document itself, holding no section, so a
    top-level heading is counted and parented by the same code as any other.
    """

    level: int = 0
    section: models.MarkdownSection | None = None
    numbered: int = 0
    appendices: int = 0


@dataclass(frozen=True)
class _Heading:
    """A paragraph that opens a section: what it says, and where it sits.

    `level` is relative to the headings around it rather than Word's own absolute
    one. An appendix declares a top level outright and is lettered rather than
    numbered, so which kind of heading it is travels with it.
    """

    text: str
    level: int
    appendix: bool


def _extract_sections(
    document: docx.document.Document,
) -> tuple[list[models.MarkdownSection], dict[str, models.MarkdownSection]]:
    """Turn the document's headings into a flat list of sections in document order.

    Word does not put the number in the text. Numbering is attached to the heading
    *styles*, so Word generates "4.3.1.1" when it renders the page while the paragraph
    itself says only its title. The number is therefore derived here, from nothing but
    each heading's level relative to the one before it.

    Levels are read as relative, never absolute: a document that opens at Heading 2
    still starts at 1, and a heading that skips a level nests one deep rather than
    leaving a gap in the number. An appendix is the exception that proves it: its
    style declares a top level outright, and it is lettered rather than numbered.

    Every other paragraph is content, and belongs to the section opened most recently
    whatever its level. Anything ahead of the first heading is not: what sits there
    is the cover page and the contents, and a contents page is regenerated from the
    headings anyway.

    Two kinds of content are not one paragraph to one block. Consecutive list items
    are held open and rendered together, because what makes a Markdown list is the
    items standing next to each other; and so is a run of paragraphs Word drew a
    border round, which is a box like any other box and has to be gathered before it
    can be rendered as one. A table is neither: it is a block of the body in its own
    right, and closes any run open when it arrives.

    The bookmarks a cross-reference can point at are collected on the way past. One
    is claimed only where it marks the start of a section, that being the whole of
    what a number can be derived for. A bookmark landing anywhere else is better left
    as the raw name Word wrote than sent confidently to the wrong place.
    """
    sections: list[models.MarkdownSection] = []
    bookmarks: dict[str, models.MarkdownSection] = {}
    run = _OpenRun()
    boxed: list[Any] = []
    # The document's own frame is never popped: it sits at level 0, and a heading's
    # level is never lower than 1.
    stack = [_OpenSection()]

    for element, opened_ahead_of_it in _body_items(document):
        if element.tag != qn("w:p"):
            _close_box(sections, run, boxed, document)
            _take_whole_block(sections, run, element, document)
            continue

        paragraph = Paragraph(element, document)
        heading = _heading_of(paragraph)

        # A box drawn by bordering the paragraphs themselves is held until the first
        # element that is not part of it, because nothing but that says where it
        # ends. A heading is never part of one: a border round a heading is emphasis,
        # and swallowing it into a quote would lose the section it opens.
        if heading is None and borders.is_boxed(element):
            _take_bordered(sections, run, boxed, element, document)
            continue

        _close_box(sections, run, boxed, document)

        if heading is None:
            _collect_body(sections, run, paragraph)
            continue

        _close_list(sections, run)
        section = _open_section(stack, heading)
        sections.append(section)

        names = opened_ahead_of_it + _bookmark_names(paragraph._p)
        bookmarks.update(dict.fromkeys(names, section))

    _close_box(sections, run, boxed, document)
    _close_list(sections, run)
    return sections, bookmarks


def _take_whole_block(
    sections: list[models.MarkdownSection],
    run: _OpenRun,
    element: Any,
    document: docx.document.Document,
) -> None:
    """Render the body element that is not a paragraph: a table, or a text box.

    Those are the only two the walk yields beside a paragraph, and they differ in
    what they do to a list open around them. A table is a block of the body in its
    own right and closes one. A text box is a box like any other - what the item
    before it introduces - so it joins that item and closes nothing.
    """
    if element.tag == textboxes.TEXT_BOX:
        _take_box(sections, run, textboxes.markdown(element, document))
        return

    _close_list(sections, run)
    _append_block(sections, tables.table_markdown(element, document))


def _heading_of(paragraph: Paragraph) -> _Heading | None:
    """The section this paragraph opens, or None where it opens none.

    A paragraph that is not a heading holds content. So does a heading with nothing
    in it, which is a layout artefact rather than a section - numbering it would put
    a section in the output that the document does not have. Answering both with the
    same None is what lets the walk ask the question once.
    """
    appendix = _is_appendix(paragraph)
    level = _APPENDIX_LEVEL if appendix else _heading_level(paragraph)
    text = paragraph.text.strip()

    if level is None or not text:
        return None
    return _Heading(text=text, level=level, appendix=appendix)


def _open_section(
    stack: list[_OpenSection], heading: _Heading
) -> models.MarkdownSection:
    """Close every frame this heading is level with or inside, and open one below.

    The frames left standing are the ones the heading is beneath, so the topmost of
    them is its parent - which is the whole of how a relative level becomes a place
    in the tree. The document's own frame sits at level 0 and so is never popped.
    """
    while stack[-1].level >= heading.level:
        stack.pop()

    section = _open_beneath(stack[-1], heading.text, appendix=heading.appendix)
    stack.append(_OpenSection(level=heading.level, section=section))
    return section


def _open_beneath(
    parent: _OpenSection, heading: str, *, appendix: bool
) -> models.MarkdownSection:
    """Open a section beneath `parent`, taking the next ordinal of its own kind.

    Appendices and numbered sections are counted apart, so an annex following
    section 7 is A rather than 8, and a numbered heading after that annex is 8.

    The link is made both ways here, which is the only place both ends are known.
    The document's own frame holds no section, so a top-level heading is linked to
    nothing - and "has no parent" and "is in no section's children" stay one fact.
    """
    if appendix:
        parent.appendices += 1
        ordinal = parent.appendices
    else:
        parent.numbered += 1
        ordinal = parent.numbered

    section = models.MarkdownSection(
        heading=heading, ordinal=ordinal, parent=parent.section, appendix=appendix
    )
    if parent.section is not None:
        parent.section.children.append(section)
    return section


def _body_items(
    document: docx.document.Document,
) -> Iterator[tuple[Any, list[str]]]:
    """The body's blocks in document order, each with the bookmarks ahead of it.

    Elements are yielded as Word wrote them rather than as python-docx proxies, so
    that deciding what a block is stays with the caller and this walk has only the
    one job. `document.paragraphs` would return today's paragraphs and go on doing
    so, but it cannot see a table, and a table is a block of the body like any other.

    The walk also carries the bookmark names opened between one block and the next: a
    w:bookmarkStart marking a heading sits either inside that heading's paragraph or
    as a sibling just ahead of it, and a walk seeing only paragraphs would miss half
    of them. Names not claimed by the block that follows are dropped with it.
    """
    opened: list[str] = []
    for element in document.element.body:
        if element.tag == qn("w:bookmarkStart"):
            opened.extend(_bookmark_names(element))
        elif element.tag in _BODY_BLOCKS:
            yield element, opened
            opened = []
            if element.tag == qn("w:p"):
                # A text box is a block of the page that Word wrote inside a run
                # rather than beside it, so no walk of the body's own children can
                # reach it. Yielding it straight after the paragraph it hangs from is
                # what files it under the section that paragraph belongs to. It
                # claims no bookmarks: the ones opened here were the paragraph's.
                for box in textboxes.anchored_in(element):
                    yield box, []


def _bookmark_names(element: Any) -> list[str]:
    """Every bookmark name opened by this element or anything beneath it."""
    starts = element.iter(qn("w:bookmarkStart"))
    return [name for start in starts if (name := start.get(qn("w:name")))]


@dataclass
class _OpenRun:
    """The run of list items being gathered, and the prose interrupting it.

    Prose between two items is held rather than filed, because what it is depends on
    the item that follows it. Where that item is drawn deeper than the one before,
    the page is showing a sub-list with an unbulleted lead-in above it, and closing
    the run at the prose would reopen the sub-list at the margin with every item of
    it a level too shallow. Held, the prose becomes a block of the item it follows
    and the sub-list nests inside that, which is the only shape Markdown has for it.

    Where the next item is not deeper there is no such relationship to keep, so the
    run closes as it always did and the prose is a block in its own right - which is
    what it looks like on the page, and what keeps a list from swallowing every
    paragraph that happens to sit between it and the next one.
    """

    items: list[tuple[lists.ListItem | None, str]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    @property
    def floor(self) -> lists.ListItem | None:
        """The leftmost item of the run, which a new one is measured against.

        The leftmost and not the latest, because it is the leftmost that Markdown
        draws at the margin: a run reopened after prose starts there, so an item the
        page draws further right than the run began is one a fresh run would put a
        level too shallow - whether or not the item just before it was there too.
        """
        items = [item for item, _ in self.items if item is not None]
        return min(items, key=lambda item: item.indent) if items else None


def _collect_body(
    sections: list[models.MarkdownSection],
    run: _OpenRun,
    paragraph: Paragraph,
) -> None:
    """Take one paragraph of the body: another item of the open list run, or prose.

    The list question is asked here rather than at the top of the walk, and that
    ordering is the whole of the guard on it: a document may attach numbering to its
    heading styles as well, so a rule reading numbering alone would bullet every
    heading in it. By the time a paragraph arrives here it is already not a heading.

    Contents entries are left out wherever they turn up, and are not list items
    however a document numbers them. They sit ahead of every heading in both real
    guides and so never reach here, but a document whose contents page opens with a
    heading of its own would otherwise file its whole table of contents as prose.
    """
    if _is_contents(paragraph):
        _close_list(sections, run)
        return

    item = lists.list_item(paragraph)
    markdown = inline.paragraph_markdown(paragraph)
    if item is not None:
        _take_pending(sections, run, item)
        run.items.append((item, markdown))
        return

    # A paragraph saying nothing changes nothing, and that includes not closing the
    # run. Word spaces its lists with empty paragraphs, and closing on one splits a
    # single list into two blocks: the blank line between them makes it a *loose*
    # list, which the editor rewrites as the one tight list it always was.
    if not markdown:
        return

    if run.items:
        run.pending.append(markdown)
        return

    _append_block(sections, markdown)


def _take_box(
    sections: list[models.MarkdownSection], run: _OpenRun, markdown: str
) -> None:
    """File a box Word drew between two list items as a block of the item above it.

    A box anchored on an item is what that item introduces - the case note the step
    says to add, the template it says to use - and a reader reads it there. Closing
    the run at one instead ends the list, so the items after it reopen at the margin
    and every step the page draws between them is thrown away with the nesting.

    A box arriving while prose is held joins the prose rather than the item, so that
    the two are settled together and stay in the order the page puts them in. With
    no run open at all it is a block of its own, which is where it stands.
    """
    if not run.items:
        _close_list(sections, run)
        _append_block(sections, markdown)
    elif run.pending:
        run.pending.append(markdown)
    else:
        run.items.append((None, markdown))


def _take_bordered(
    sections: list[models.MarkdownSection],
    run: _OpenRun,
    boxed: list[Any],
    element: Any,
    document: docx.document.Document,
) -> None:
    """Take one bordered paragraph into the box being gathered.

    Where its border differs from the one before it, Word drew a fresh frame rather
    than growing the open one, so the box held so far is finished and this paragraph
    opens the next. That is what puts the line between an email template's subject
    and its body: two boxes on the page are two blockquotes, not one.
    """
    if boxed and borders.signature(element) != borders.signature(boxed[-1]):
        _close_box(sections, run, boxed, document)

    boxed.append(element)


def _close_box(
    sections: list[models.MarkdownSection],
    run: _OpenRun,
    boxed: list[Any],
    document: docx.document.Document,
) -> None:
    """File the run of bordered paragraphs held so far, and start a fresh one.

    It is filed exactly where a text box anchored in the same place would be, because
    it is the same box: what closes the run of bordered paragraphs is the first
    element without the border, and that element has not been taken yet.
    """
    if not boxed:
        return

    markdown = borders.markdown(boxed, document)
    boxed.clear()
    _take_box(sections, run, markdown)


def _take_pending(
    sections: list[models.MarkdownSection], run: _OpenRun, item: lists.ListItem
) -> None:
    """Settle the prose held since the last item, now that the next one is known."""
    if not run.pending:
        return

    floor = run.floor
    if floor is not None and lists.is_deeper(item, floor):
        run.items.extend((None, block) for block in run.pending)
        run.pending.clear()
        return

    _close_list(sections, run)


def _close_list(sections: list[models.MarkdownSection], run: _OpenRun) -> None:
    """File the open run of list items as one block, and open a fresh run.

    A run is closed by anything that is not a list item and is not held for it - a
    heading, a table, a paragraph the item after it did not claim, or the end of the
    document - so it always lands in the section it started in. Prose held for an
    item that never came is filed after the list, where it stands on the page.
    """
    if run.items:
        _append_block(sections, lists.render(run.items))
        run.items.clear()

    for block in run.pending:
        _append_block(sections, block)
    run.pending.clear()


def _append_block(sections: list[models.MarkdownSection], block: str) -> None:
    """Add one finished block of Markdown to the open section, where there is one.

    Anything ahead of the first heading has no section to belong to and is dropped.
    What sits there is the cover page and the contents, neither of which is content
    the conversion is meant to carry.
    """
    if not sections or not block:
        return

    section = sections[-1]
    section.content = f"{section.content}\n\n{block}" if section.content else block


def _is_appendix(paragraph: Paragraph) -> bool:
    """Whether this paragraph opens an appendix, which only its style can say.

    An annex style carries no outline level and is not a "Heading n", so there is
    nothing else to read it from. Detection is by style alone, deliberately: a
    "Heading n" whose text happens to read "Annex A" is already a section by the
    rule below, and would gain a letter rather than an existence.
    """
    return _style_name(paragraph).startswith(_APPENDIX_STYLE_PREFIXES)


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
