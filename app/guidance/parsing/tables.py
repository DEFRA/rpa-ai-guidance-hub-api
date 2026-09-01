"""Turn a Word table into the Markdown it says.

Word writes a table as a grid of cells, and a cell as a little document of its own:
paragraphs, with all the runs, hyperlinks and numbering any other paragraph has. So a
cell is rendered by the same machinery as body prose rather than by reading its text.
Reading it as text instead is what silently drops every hyperlink, every mark and every
list marker a cell holds.

python-docx's own `row.cells` must not be used here, nor `cell.text`. `row.cells`
expands the grid: it hands back the same w:tc once per column a gridSpan covers, and
the cell above for every vertical-merge continuation, so a merged cell arrives as
several identical ones and its text is emitted once per grid position it covers. The
walk below reads w:tr and w:tc directly, where Word writes each cell exactly once.

Two shapes come out of it. A table of one cell is not a table at all: Word uses it as
a callout box, and it becomes a blockquote, which can hold the several paragraphs all
nine of the real ones have. Everything else becomes a GFM pipe table, whose rows
cannot contain a newline - so a cell's blocks are joined with <br> instead, and its
pipes are escaped, both of which are about the row surviving rather than about how
the text reads.

`callout` is public because a one-cell table is not the only box Word draws: a text
box holds the same thing, and `parser` renders one through here rather than growing a
third copy of the paragraphs-to-blocks walk.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.guidance.parsing import inline, lists
from app.guidance.parsing.ooxml import W_VAL

if TYPE_CHECKING:
    from collections.abc import Iterator

# A vertical merge is a continuation unless it says it is the cell that starts one.
_MERGE_ORIGIN = "restart"

# Anything a pipe row cannot contain. A hard line break arrives as a backslash and a
# newline, and the backslash goes with it: it marks a break there is no longer room
# for. Block joins produce the rest.
_ROW_BREAK = re.compile(r"\\?\n")

_CELL_BREAK = "<br>"

# A pipe ends a cell wherever it appears, so a pipe the document means as text has to
# say so. This is not the escaping feature: an unescaped pipe adds a column, where an
# unescaped asterisk only reads oddly.
_ESCAPED_PIPE = r"\|"


def table_markdown(table: Any, parent: Any) -> str:
    """One table as a Markdown block, or "" where it says nothing.

    `parent` is what the cell's paragraphs are built against, so that a hyperlink in
    a cell can still be resolved through the document part's relationships.
    """
    rows = table.findall(qn("w:tr"))
    columns = len(table.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}"))
    if not rows or not columns:
        return ""

    if len(rows) == 1 and len(rows[0].findall(qn("w:tc"))) == 1:
        return callout(rows[0].findall(qn("w:tc"))[0], parent)

    header, *body = rows
    lines = [_row(header, columns, parent), _delimiter(columns)]
    lines.extend(_row(row, columns, parent) for row in body)
    return "\n".join(lines)


def callout(container: Any, parent: Any) -> str:
    """The paragraphs of a box Word drew, as a blockquote.

    `container` is anything whose own w:p children are its blocks - the w:tc of a
    one-cell table, or the w:txbxContent of a text box. Word draws a box either way
    and means the same thing by it, so they render the same way.

    The paragraphs are kept as paragraphs. Reading the container as a single string
    puts them all on one line - or, once a blockquote marker is in front of it, breaks
    out of the quote at the first newline.
    """
    quoted: list[str] = []
    for block in _own_blocks(container, parent):
        if quoted:
            quoted.append(">")
        quoted.extend(f"> {line}" for line in block.split("\n"))

    return "\n".join(quoted)


def _row(row: Any, columns: int, parent: Any) -> str:
    """One row as a pipe row, holding exactly as many columns as the grid declares."""
    cells = list(_columns_of(row))[:columns]
    cells += [None] * (columns - len(cells))
    return "| " + " | ".join(_cell_markdown(cell, parent) for cell in cells) + " |"


def _delimiter(columns: int) -> str:
    """The row under the header, which is what makes a pipe table a table."""
    return "| " + " | ".join(["---"] * columns) + " |"


def _columns_of(row: Any) -> Iterator[Any | None]:
    """Each grid column this row covers, as the cell filling it or as nothing.

    Markdown spans neither columns nor rows, so a merge is rendered by putting the
    content in the first column it covers and leaving the rest empty. The words then
    appear exactly as often as the document says them, which is also what stops the
    conversion audit from crediting itself with content Word does not have.
    """
    for cell in row.findall(qn("w:tc")):
        yield None if _continues_merge(cell) else cell
        for _ in range(_span(cell) - 1):
            yield None


def _continues_merge(cell: Any) -> bool:
    """Whether this cell carries on the one above rather than starting a merge."""
    merge = _property(cell, "w:vMerge")
    return merge is not None and merge.get(qn(W_VAL)) != _MERGE_ORIGIN


def _span(cell: Any) -> int:
    """How many grid columns this cell covers."""
    element = _property(cell, "w:gridSpan")
    return 1 if element is None else int(element.get(qn(W_VAL)))


def _property(cell: Any, name: str) -> Any:
    """One of a cell's properties, or None where it does not set it.

    Read through the w:tcPr rather than from it, because a cell setting no property
    at all writes no w:tcPr either, and that is not a case worth a guard of its own.
    """
    return cell.find(f"{qn('w:tcPr')}/{qn(name)}")


def _cell_markdown(cell: Any | None, parent: Any) -> str:
    """One cell as the single line a pipe row can hold.

    Everything that would have been a line of its own - a second paragraph, a list
    item, a hard break - becomes a <br>, because a newline anywhere in a row ends the
    row and orphans what follows it.
    """
    if cell is None:
        return ""

    blocks = _CELL_BREAK.join(_own_blocks(cell, parent))
    return _ROW_BREAK.sub(_CELL_BREAK, blocks).replace("|", _ESCAPED_PIPE)


def _own_blocks(container: Any, parent: Any) -> list[str]:
    """A container's own paragraphs as Markdown blocks, in order.

    Consecutive numbered paragraphs are gathered into one list block, as they are in
    the body. The gathering is written out again here rather than shared with
    parser.py's: the two agree on nothing but `lists`' own two functions, and already
    disagree on what closes a run and on how the blocks that come out are joined.

    Only the container's own paragraphs are read, which is what "own" is doing in the
    name. A table nested inside a cell is left alone - there are none in either real
    guide, and one would need a table's shape inside a cell that cannot hold a line
    break. A text box nested inside a text box is left alone for the same reason.
    """
    blocks: list[str] = []
    run: list[tuple[lists.ListItem, str]] = []

    for element in container.findall(qn("w:p")):
        paragraph = Paragraph(element, parent)
        markdown = inline.paragraph_markdown(paragraph)

        item = lists.list_item(paragraph)
        if item is not None:
            run.append((item, markdown))
            continue

        _append(blocks, _close_run(run))
        _append(blocks, markdown)

    _append(blocks, _close_run(run))
    return blocks


def _close_run(run: list[tuple[lists.ListItem, str]]) -> str:
    """The open run of list items as one block, leaving the run open and empty."""
    block = lists.render(run)
    run.clear()
    return block


def _append(blocks: list[str], block: str) -> None:
    """Add a block to a cell, where it says anything.

    An empty paragraph is not a block, and neither is a run of list items that all
    turned out to say nothing. Word writes an empty paragraph into every cell it
    creates, so this is the ordinary case and not a defensive one.
    """
    if block:
        blocks.append(block)
