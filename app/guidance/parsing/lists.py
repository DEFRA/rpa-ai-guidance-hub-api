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

Levels are read relatively, exactly as headings are: a run opening at ilvl 1 still
starts at the top, and a jump from ilvl 0 to ilvl 2 nests one deep rather than two.
Word's own indentation cannot be the signal instead: the same numId and ilvl can be
indented differently in different places, according to whatever the author dragged.
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


@dataclass(frozen=True)
class ListItem:
    """What Word says about one item: how deep it sits, and how it is marked.

    `level` is the raw w:ilvl. It is a position in Word's own scheme rather than a
    depth in the output, and is turned into one only in the company of the items
    around it.
    """

    level: int = _TOP_LEVEL
    ordered: bool = False


def list_item(paragraph: Paragraph) -> ListItem | None:
    """The list item this paragraph is, or None where numbering does not apply."""
    num_id = _numbering_value(paragraph, "w:numId")
    if num_id is None or num_id == _NUMBERING_REMOVED:
        return None

    level = int(_numbering_value(paragraph, "w:ilvl") or _TOP_LEVEL)
    return ListItem(level=level, ordered=_is_ordered(paragraph, num_id, level))


def render(items: Sequence[tuple[ListItem, str]]) -> str:
    """One contiguous run of items, each with its own Markdown, as a single block.

    An item saying nothing is left out rather than bulleted, exactly as an empty
    paragraph is, and does not spend an ordinal on the way past.
    """
    lines: list[str] = []
    stack: list[_OpenLevel] = []

    for item, markdown in items:
        if not markdown:
            continue

        depth = _depth_for(stack, item)
        marker = depth.next_marker()
        item_markdown = _hanging(markdown, depth.content_column)
        lines.append(f"{depth.indent}{marker}{item_markdown}")

    return "\n".join(lines)


@dataclass
class _OpenLevel:
    """One depth of an open run: what it is counting, and where its items sit.

    `indent` is the column this depth's markers start in, and is fixed when the
    depth opens. `content_column` is where the text of its most recent item starts,
    which is both where that item's own later lines hang and where a child of it is
    indented to; `next_marker` records it on the way out.
    """

    level: int
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


def _depth_for(stack: list[_OpenLevel], item: ListItem) -> _OpenLevel:
    """The depth this item belongs to, opening one where the item goes deeper.

    A depth opened again after being closed starts counting from one, while the
    depth returned to carries on - which is what numbers a list 1, 2, 3 rather than
    1, 1, 1 when a sub-list interrupts it.
    """
    while stack and stack[-1].level > item.level:
        stack.pop()

    if stack and stack[-1].level == item.level:
        depth = stack[-1]
        # A bullet interrupting an ordered list at the same depth is a different
        # list, so what its neighbour had counted to says nothing about it.
        if depth.ordered != item.ordered:
            depth.ordered = item.ordered
            depth.count = 0
        return depth

    indent = stack[-1].content_column if stack else ""
    stack.append(_OpenLevel(level=item.level, ordered=item.ordered, indent=indent))
    return stack[-1]


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


def _is_ordered(paragraph: Paragraph, num_id: str, level: int) -> bool:
    """Whether this list numbers its items at this level rather than bulleting them.

    Where the list declares nothing at this level its first level decides, that
    being the only thing the document does say about it.
    """
    formats = _declared_formats(paragraph, num_id)
    if not formats:
        return False

    return formats.get(level, next(iter(formats.values()))) != _BULLET_FORMAT


def _declared_formats(paragraph: Paragraph, num_id: str) -> dict[int, str]:
    """The format a list declares at each of its levels, as ilvl -> numFmt.

    Every way a document can say nothing about a list - declaring no numbering at
    all, carrying no w:num for this numId, defining no levels beneath it - means
    the same thing to a reader and gives the same answer, so all of them arrive
    here as one empty collection rather than as a guard apiece. python-docx's own
    `numbering_part` cannot express the first of them: it raises NotImplementedError
    rather than saying the part is absent.
    """
    return {
        int(ilvl): value
        for part in _numbering_parts(paragraph)
        for definition in _definitions_of(part.element, num_id)
        for level in definition.findall(qn("w:lvl"))
        if (ilvl := level.get(qn("w:ilvl"))) is not None
        for value in _values(level, "w:numFmt")
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
