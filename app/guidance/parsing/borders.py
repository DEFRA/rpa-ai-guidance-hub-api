"""Read the box Word draws by bordering the paragraphs themselves.

Word has three ways of putting a box round something and means the same thing by all
of them. Two arrive as a container whose children are the box's blocks - the w:tc of a
one-cell table, the w:txbxContent of a text box - and `tables` and `textboxes` render
those. The third has no container at all: the author selects a few ordinary body
paragraphs and gives each of them a border, and the box exists only in that the
paragraphs sharing it happen to be adjacent. Assembling that run is the whole of what
this module does; what comes out of it is the same blockquote as the other two.

It is the form the guides reach for whenever they quote something the reader is meant
to copy rather than follow - a case note to paste, an email to send, a proforma
comment. The border is the only thing separating the quoted text from the guidance
that resumes underneath it, so without it the instruction after a template reads as
the next line of the template.

A run of them is one box only while the border stays the same. Word draws a fresh
frame wherever the properties change, which is how these guides put an email's subject
line in a box of its own above the box holding its body - see `signature`.

Only a border on all four sides is one. A border on one side is a rule, not a box:
Word's own `Title` style underlines itself with a bottom border, and quoting a heading
because of it would be a loss dressed up as a gain. Only direct formatting is read,
for the same reason - the styles that carry a border in these guides carry a one-sided
one, and a style is where a decorative rule is declared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docx.oxml.ns import qn

from app.guidance.parsing import tables
from app.guidance.parsing.ooxml import W_PPR

if TYPE_CHECKING:
    from collections.abc import Iterable

# What a box has and a rule does not, and the whole of what either rule below reads.
# Word writes w:between as well where consecutive bordered paragraphs share one box,
# and it is a line drawn inside a frame rather than anything about which frame.
_SIDES = ("w:top", "w:left", "w:bottom", "w:right")

# A side declared and then turned off. Word writes it rather than dropping the
# element, so the presence of the four sides is not on its own the presence of a box.
_ABSENT = frozenset({"none", "nil"})


def is_boxed(element: Any) -> bool:
    """Whether this body element is a paragraph Word drew a box around."""
    if element.tag != qn("w:p"):
        return False

    properties = element.find(qn(W_PPR))
    if properties is None:
        return False

    border = properties.find(qn("w:pBdr"))
    return border is not None and all(_drawn(border, side) for side in _SIDES)


def signature(element: Any) -> tuple[Any, ...]:
    """What decides whether two bordered paragraphs share one box or start two.

    Word merges consecutive bordered paragraphs into a single frame only while their
    border properties match exactly, and draws a fresh box the moment anything
    differs - a difference that renders the same included, `w:color="auto"` against
    an explicit black. That looks like a distinction without meaning and is not: it
    is the whole of what separates an email template's subject box from its body box,
    and a case note's hold code from its fields, in every guide that draws them apart.

    Read off the four sides as Word wrote them rather than off any one attribute,
    because which attribute carries the difference is the author's business and not
    ours. The four and no more: w:between is what Word draws between paragraphs that
    are already in one frame, so a run carrying it on some of its paragraphs and not
    others would be split here on the strength of the very thing saying it is one box.
    """
    properties = element.find(qn(W_PPR))
    if properties is None:
        return ()

    border = properties.find(qn("w:pBdr"))
    if border is None:
        return ()

    return tuple(
        (side, tuple(sorted(edge.attrib.items())))
        for side in _SIDES
        if (edge := border.find(qn(side))) is not None
    )


def markdown(paragraphs: Iterable[Any], parent: Any) -> str:
    """A run of bordered paragraphs as one blockquote.

    `parent` is what they are built against, so that a hyperlink inside one still
    resolves through the document part's relationships.
    """
    return tables.quote(tables.blocks(paragraphs, parent))


def _drawn(border: Any, side: str) -> bool:
    """Whether one side of a border is a line rather than a declared absence."""
    edge = border.find(qn(side))
    return edge is not None and edge.get(qn("w:val")) not in _ABSENT
