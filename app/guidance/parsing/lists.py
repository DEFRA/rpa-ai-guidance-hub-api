"""Turn the numbering Word attaches to a paragraph into a Markdown list.

Word does not write a list as a structure. It writes each item as an ordinary
paragraph carrying a w:numPr - a numId naming the list and an ilvl naming the depth -
and generates the bullet or the digit when it renders the page. Neither is in the
text, so an ordered list arrives with its ordinals missing entirely - which matters
for a numbered list whose own items refer the reader back to a step by its number.

What makes a paragraph a list item is therefore numbering being in effect on it, and
nothing else. Not its style's name: an author can bullet a paragraph styled plain
"Normal", so a list of style names finds only the items that happen to be styled as
lists. The rule reads a numId from the paragraph's own properties, else from the ones
its style declares, with numId="0" meaning that numbering has been taken *off* this
paragraph and winning over the style - which is how a deliberately un-bulleted lead-in
line stays prose.

The rule is only safe because of where it is asked. A document may attach numbering to
its *heading* styles as well, so parser.py asks the heading question first and never
reaches here with one.

How deep an item sits comes from the indent Word draws it at, not from its ilvl.
The two usually agree, because a list definition declares an indent for each of its
levels. Where they part, it is because the author made the sub-list by starting a
further-indented list of its own rather than by demoting into the one above it -
which is what pasting between documents leaves behind, and is how most of the nesting
in these guides is written. The page shows an indented sub-list either way; ilvl
alone reads one flat run. What ilvl does still decide is the marker, the format a
list declares at a level being the whole of what says bullet or number.

Indents are read relatively, exactly as heading levels are: a run opening indented
still starts at the left, and a jump of two levels' worth nests one deep rather than
two. A difference smaller than half of Word's own quarter-inch step is not a nesting
at all - the same depth is drawn a few twips either way wherever an author has
dragged an item - so anything under that leaves the item where its neighbour is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

from app.guidance.parsing.ooxml import W_VAL

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docx.text.paragraph import Paragraph

# A numId of zero does not name a list: it says this paragraph has had numbering
# removed, which is why it has to beat the numId the paragraph's style declares.
_NUMBERING_REMOVED = "0"

# The one format that is not a number. Word defines lowerLetter and lowerRoman as
# well, but Markdown has only the two kinds, so every other format is an ordered list.
_BULLET_FORMAT = "bullet"

_BULLET_MARKER = "-"

_TOP_LEVEL = 0

# The margin itself, where Word draws an item nothing has indented.
_NO_INDENT = 0

# How far apart two items can be drawn and still be at the same depth, in twips.
# Word indents by a quarter of an inch when it demotes an item, so half of that is
# wide enough to hold every nudge an author leaves behind and narrower than any
# nesting they can have meant.
_SAME_COLUMN = 180


@dataclass(frozen=True)
class ListItem:
    """What Word says about one item: where it is drawn, and how it is marked.

    `indent` is how far from the margin Word puts it, in twips. It is a position on
    the page rather than a depth in the output, and is turned into one only in the
    company of the items around it.
    """

    indent: int = _NO_INDENT
    ordered: bool = False


def list_item(paragraph: Paragraph) -> ListItem | None:
    """The list item this paragraph is, or None where numbering does not apply."""
    num_id = _numbering_value(paragraph, "w:numId")
    if num_id is None or num_id == _NUMBERING_REMOVED:
        return None

    level = int(_numbering_value(paragraph, "w:ilvl") or _TOP_LEVEL)
    return ListItem(
        indent=_indent(paragraph, num_id, level),
        ordered=_is_ordered(paragraph, num_id, level),
    )


def is_deeper(item: ListItem, previous: ListItem) -> bool:
    """Whether Word draws this item further right than that one by a real step."""
    return item.indent > previous.indent + _SAME_COLUMN


def render(items: Sequence[tuple[ListItem | None, str]]) -> str:
    """One contiguous run of items, each with its own Markdown, as a block.

    An entry with no item is a block of prose that belongs to the item above it -
    a lead-in that Word leaves unbulleted between an item and a sub-list of it. It
    is indented into that item, which is the only place Markdown has for it: left in
    the first column it would end the list, and the sub-list after it would reopen
    at the margin with every item of it a level too shallow.

    A run is not always one list: changing from bullets to numbers starts another,
    and where that happens at the outermost depth the two are parted by a blank
    line. That is what the editor writes, and matching it is what stops a converted
    document being rewritten by the first save. A blank line is *not* written for a
    change deeper in, because there it would make the list containing it loose.

    An item saying nothing is left out rather than bulleted, exactly as an empty
    paragraph is, and does not spend an ordinal on the way past.
    """
    lines: list[str] = []
    stack: list[_OpenLevel] = []

    for item, markdown in items:
        if not markdown:
            continue

        if item is None:
            # Nothing open for it to belong to means it is not a continuation at
            # all, and the caller has already filed it as a block of its own.
            if stack:
                lines.append("")
                lines.append(_continuation(markdown, stack[-1].content_column))
            continue

        depth, opened_a_list = _depth_for(stack, item)
        if opened_a_list and len(stack) == 1 and lines:
            lines.append("")

        marker = depth.next_marker()
        item_markdown = _hanging(markdown, depth.content_column)
        lines.append(f"{depth.indent}{marker}{item_markdown}")

    return "\n".join(lines)


@dataclass
class _OpenLevel:
    """One depth of an open run: what it is counting, and where its items sit.

    `left` is the column Word draws this depth in, and is the one the items joining
    it are measured against. `indent` is the column its markers start in *here*, and
    is fixed when the depth opens. `content_column` is where the text of its most
    recent item starts, which is both where that item's own later lines hang and
    where a child of it is indented to; `next_marker` records it on the way out.
    """

    left: int
    ordered: bool
    indent: str = ""
    count: int = 0
    content_column: str = ""

    def next_marker(self) -> str:
        """This depth's next marker, and the column it puts its item's text in.

        An ordered marker is as wide as its own ordinal, so the tenth item indents
        its children one column further than the ninth. Taking the width from the
        marker actually written is what keeps a child of either kind of parent in
        its parent's content column, which is where CommonMark requires it.
        """
        self.count += 1
        marker = f"{self.count}. " if self.ordered else f"{_BULLET_MARKER} "
        self.content_column = self.indent + " " * len(marker)
        return marker


def _depth_for(stack: list[_OpenLevel], item: ListItem) -> tuple[_OpenLevel, bool]:
    """The depth this item belongs to, and whether it starts a list of its own.

    A depth opened again after being closed starts counting from one, while the
    depth returned to carries on - which is what numbers a list 1, 2, 3 rather than
    1, 1, 1 when a sub-list interrupts it.

    Going deeper opens a nested list, which the caller does not part with a blank
    line; only a change of kind at a depth already open does, and saying which
    happened is why the answer is a pair.

    A depth is closed only by an item drawn to the left of it by more than a nudge,
    and an item joins the innermost depth standing in its own column. Anything
    further right than all of them starts a depth of its own, however far right:
    what a document says about how deep an item sits is only where its neighbours
    are, so two levels' worth of indent nests one deep, exactly as it does for
    headings.
    """
    while stack and stack[-1].left - item.indent > _SAME_COLUMN:
        stack.pop()

    if stack and abs(stack[-1].left - item.indent) <= _SAME_COLUMN:
        depth = stack[-1]
        # A bullet interrupting an ordered list at the same depth is a different
        # list, so what its neighbour had counted to says nothing about it.
        if depth.ordered != item.ordered:
            depth.ordered = item.ordered
            depth.count = 0
            return depth, True
        return depth, False

    indent = stack[-1].content_column if stack else ""
    stack.append(_OpenLevel(left=item.indent, ordered=item.ordered, indent=indent))
    return stack[-1], False


def _continuation(markdown: str, content_column: str) -> str:
    """A block belonging to the item above it, indented into that item's text.

    Every line of it, the first included - which is what parts it from `_hanging`,
    where the first line follows a marker already standing in that column.
    """
    return content_column + markdown.replace("\n", f"\n{content_column}")


def _hanging(markdown: str, content_column: str) -> str:
    """An item's Markdown, with any line after the first hung under its own text.

    A paragraph's only line breaks are the hard ones inline.py writes. Left in the
    first column they would close the list; indented to the item's own text they
    stay part of it.
    """
    return markdown.replace("\n", f"\n{content_column}")


def _numbering_value(paragraph: Paragraph, name: str) -> str | None:
    """One numbering property in effect on a paragraph: its own, else its style's.

    Direct formatting overrides a style property by property rather than wholesale,
    so a paragraph setting only its own w:ilvl still takes its w:numId from the
    style it is in.
    """
    sources = (paragraph._p.find(qn("w:pPr")), _style_properties(paragraph))
    values = (_numbering_property(source, name) for source in sources)
    return next((value for value in values if value is not None), None)


def _style_properties(paragraph: Paragraph) -> Any:
    """The paragraph properties the paragraph's style declares.

    Written as an expression for the same reason parser.py's _style_name is:
    python-docx annotates `style` as optional but resolves an unset or unknown
    style id to the document's default, so the None it allows for never arrives.
    """
    style = paragraph.style
    return style.element.find(qn("w:pPr")) if style is not None else None


def _numbering_property(properties: Any, name: str) -> str | None:
    """The value one set of paragraph properties gives a w:numPr child."""
    if properties is None:
        return None

    element = properties.find(f"{qn('w:numPr')}/{qn(name)}")
    return element.get(qn(W_VAL)) if element is not None else None


def _indent(paragraph: Paragraph, num_id: str, level: int) -> int:
    """How far from the margin Word draws this item, in twips.

    The paragraph's own indent first and the list's definition of the level second,
    which is the order Word itself resolves one in: a definition indents every item
    of a level together, and dragging one item overrides that for it alone. Where
    neither says anything the item is drawn at the margin, and nothing else is left
    to ask - the style an item is in never reaches this, because a style carrying an
    indent but no numbering makes no list item to begin with.
    """
    own = _left_indent(paragraph._p.find(qn("w:pPr")))
    if own is not None:
        return own

    declared = _declared_levels(paragraph, num_id).get(level)
    if declared is None:
        return _NO_INDENT

    return _left_indent(declared.find(qn("w:pPr"))) or _NO_INDENT


def _left_indent(properties: Any) -> int | None:
    """The left indent one set of paragraph properties sets, where it sets one."""
    if properties is None:
        return None

    element = properties.find(qn("w:ind"))
    if element is None:
        return None

    value = element.get(qn("w:left"))
    return None if value is None else int(value)


def _is_ordered(paragraph: Paragraph, num_id: str, level: int) -> bool:
    """Whether this list numbers its items at this level rather than bulleting them.

    Where the list declares nothing at this level its first level decides, that
    being the only thing the document does say about it.
    """
    formats = {
        ilvl: value
        for ilvl, declared in _declared_levels(paragraph, num_id).items()
        for value in _values(declared, "w:numFmt")
    }
    if not formats:
        return False

    return formats.get(level, next(iter(formats.values()))) != _BULLET_FORMAT


def _declared_levels(paragraph: Paragraph, num_id: str) -> dict[int, Any]:
    """What a list declares at each of its levels, as ilvl -> w:lvl.

    Every way a document can say nothing about a list - declaring no numbering at
    all, carrying no w:num for this numId, defining no levels beneath it - means
    the same thing to a reader and gives the same answer, so all of them arrive
    here as one empty collection rather than as a guard apiece. python-docx's own
    `numbering_part` cannot express the first of them: it raises NotImplementedError
    rather than saying the part is absent.
    """
    return {
        int(ilvl): level
        for part in _numbering_parts(paragraph)
        for definition in _definitions_of(part.element, num_id)
        for level in definition.findall(qn("w:lvl"))
        if (ilvl := level.get(qn("w:ilvl"))) is not None
    }


def _numbering_parts(paragraph: Paragraph) -> list[Any]:
    """The document's numbering definitions: the one part holding them, or none."""
    return [
        relationship.target_part
        for relationship in paragraph.part.rels.values()
        if relationship.reltype == RT.NUMBERING
    ]


def _definitions_of(numbering: Any, num_id: str) -> list[Any]:
    """The abstract definition the named list is an instance of, where it has one.

    A w:num is only a reference: the levels, and so the formats, live on the
    w:abstractNum it names.
    """
    abstract_ids = [
        value
        for num in numbering.findall(qn("w:num"))
        if num.get(qn("w:numId")) == num_id
        for value in _values(num, "w:abstractNumId")
    ]
    return [
        definition
        for definition in numbering.findall(qn("w:abstractNum"))
        if definition.get(qn("w:abstractNumId")) in abstract_ids
    ]


def _values(parent: Any, name: str) -> list[str]:
    """The w:val of each named child, leaving out any child that carries none."""
    return [
        value
        for child in parent.findall(qn(name))
        if (value := child.get(qn(W_VAL))) is not None
    ]
