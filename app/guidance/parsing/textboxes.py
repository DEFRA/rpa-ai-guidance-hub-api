"""Reach the boxes Word draws inside a run rather than beside it.

A text box is not a block of the body. It hangs off a w:drawing on a run, so a walk
of the body's own children cannot see it however carefully it looks, and its contents
go missing with nothing in the output to say they were ever there.

Two things have to be got right to read one. The first is finding it at all, which is
what `anchored_in` is for. The second is reading it exactly once: Word writes one copy
of a shape per consumer that might render it, DrawingML under mc:Choice and VML under
mc:Fallback, and draws precisely one of them - so a walk taking every w:txbxContent it
passes says the box twice.

What comes out is a blockquote, because a text box and a one-cell table are the same
box Word drew and it means the same thing by both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docx.oxml.ns import qn

from app.guidance.parsing import tables

if TYPE_CHECKING:
    from collections.abc import Iterator

# The tag a caller dispatches on to recognise a box among the body's other blocks.
TEXT_BOX = qn("w:txbxContent")

# Markup compatibility, spelled out because python-docx's namespace map has no "mc"
# prefix to resolve.
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_ALTERNATE_CONTENT = f"{_MC}AlternateContent"
_CHOICE = f"{_MC}Choice"
_FALLBACK = f"{_MC}Fallback"


def anchored_in(paragraph: Any) -> Iterator[Any]:
    """Every text box this paragraph anchors, taking one alternate branch.

    Reading both branches of an mc:AlternateContent says a box's contents twice: the
    same shape is written out as DrawingML under one branch and as VML under the
    other, and taking the pair puts both copies in the Markdown.

    The conversion audit walks its own copy of this rule rather than importing this
    one, and that is deliberate - it is the oracle, and a fault the two held in common
    would cancel itself out of the score instead of showing up as a loss.

    A text box inside a text box is not descended into: it is the outer box's own
    content, and emitting it here as well would say it twice for a second reason.
    """
    pending = [paragraph]
    while pending:
        element = pending.pop()
        if element.tag == TEXT_BOX:
            yield element
            continue

        pending.extend(reversed(_drawn_children(element)))


def markdown(box: Any, parent: Any) -> str:
    """One text box as a Markdown block.

    A blockquote, through the same function a one-cell table goes through. Word has
    no callout of its own and draws a box either way; rendering them alike is what
    stops the reader having to know which of the two the author reached for.

    `parent` is what the box's paragraphs are built against, so that a hyperlink
    inside one still resolves through the document part's relationships.
    """
    return tables.callout(box, parent)


def _drawn_children(element: Any) -> list[Any]:
    """The children Word draws: one branch of an alternate, all of anything else.

    The first mc:Choice is what Word takes when it understands the markup that branch
    requires, which for a document it has just written it does; mc:Fallback is the
    answer only when there is no choice at all. Where an alternate offers several
    choices the first is still taken: picking properly would mean evaluating
    mc:Requires against a table of every namespace we can render, which is a great
    deal of machinery for a distinction Word itself rarely draws.
    """
    if element.tag != _ALTERNATE_CONTENT:
        return list(element)

    branch = element.find(_CHOICE)
    if branch is None:
        branch = element.find(_FALLBACK)
    return [] if branch is None else [branch]
