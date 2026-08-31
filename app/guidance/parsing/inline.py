"""Turn a Word paragraph into the Markdown it says.

Word does not store a paragraph as text with formatting laid over it. It stores a
sequence of runs, and starts a new one wherever anything at all changes - a proofing
boundary, an edit, a language mark - so "Is your claim valid" arrives as two bold
runs, "Is " and "your claim valid". Putting a paragraph back together is this
module's work, and two rules do most of it. Both are corrections, measured against
what the PoC produced from a real guide:

- Adjacent runs wearing the same marks are one span. A marker per run breaks one
  emphasised phrase into several, and where two of them meet, into "****".
- A span's markers never wrap the space around it. Word keeps the trailing space
  inside the bold run, and "**Note: **Validation" is not emphasis under CommonMark
  at all - the reader is shown the asterisks - while an editor that tidies the space
  outwards deletes it and welds "Note:" to the word after. 15 sites in one document.
  Hoisting the space out here is what stops both.

Bold, italic and strikethrough are written as Markdown. Underline, superscript and
subscript have no Markdown, so they are written as the HTML that Markdown allows.

Colour is kept only where it says something. Word writes the default text colour out
explicitly rather than leaving it unsaid, so four fifths of the coloured runs in the
two real guides are "000000" or "auto"; most of the rest is the blue Word paints on
a hyperlink, which the output already renders as a link. What is left is a colour
the author reached for, and that is the part worth carrying.

A link is not always an element. Word's older HYPERLINK field writes the address as
an instruction between field boundaries, with the text rendered from it following,
and six of those across the two real guides reached the page as unlinked text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import groupby
from typing import TYPE_CHECKING, Any

from docx.oxml.ns import qn

from app.guidance.parsing.ooxml import W_VAL, is_toggle_on

if TYPE_CHECKING:
    from docx.text.paragraph import Paragraph

# The default text colour, written out rather than left unsaid. Word spells hex in
# upper case and "auto" in lower, so both are compared folded.
_UNCOLOURED = ("", "AUTO", "000000")

# Markdown has no tab stop, and a line that opens with one is a code block, so a tab
# is spent as the separator it is.
_TAB = " "

# CommonMark's hard line break: a backslash ending the line.
_LINE_BREAK = "\\\n"

# A break carrying no type is a soft one, which is also what "textWrapping" spells.
# The rest - page, column - are pagination, and say nothing about the text.
_SOFT_BREAK_TYPES = (None, "textWrapping")

_VERTICAL_TAGS = {"superscript": "sup", "subscript": "sub"}

# Whitespace ends a Markdown link destination and a bracket closes it, so a target
# carrying either has to be wrapped in <>. Everything else reads better bare.
_NEEDS_BRACKETS = re.compile(r"[\s()]")

# A link field's instruction, e.g. `HYPERLINK "https://example.org/a"`. Word may split
# it across several instrText runs, so it is matched once they are reassembled.
_HYPERLINK_INSTRUCTION = re.compile(r'HYPERLINK\s+"([^"]*)"', re.IGNORECASE)


class _FieldBoundary(StrEnum):
    """The three positions a fldChar marks, spelled as Word spells them."""

    BEGIN = "begin"
    SEPARATE = "separate"
    END = "end"


_FIELD_BOUNDARIES = {boundary.value: boundary for boundary in _FieldBoundary}


@dataclass(frozen=True)
class _Marks:
    """What a run is wearing. Runs wearing the same marks are one span."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    vertical: str = ""
    colour: str = ""
    link: str = ""


@dataclass(frozen=True)
class _Run:
    """A stretch of text and the marks it carries."""

    text: str
    marks: _Marks


@dataclass
class _Field:
    """One Word field, as the paragraph's runs are walked.

    A field is a run sequence rather than an element of its own: a fldChar begin,
    the runs holding the instruction that says what the field is, a separate, the
    runs Word rendered from that instruction, and an end. Instruction and result
    are both ordinary runs, so only the position in the walk tells them apart.
    """

    instruction: str = ""
    result: list[_Run] = field(default_factory=list)
    showing_result: bool = False

    def absorb(self, run: Any) -> None:
        """Take one run as the instruction or as the result, as position says."""
        if not self.showing_result:
            self.instruction += _instruction_text(run)
            return

        marks = _marks_of(run)
        self.result.extend(_Run(text, marks) for text in _run_texts(run))

    def rendered(self) -> list[_Run]:
        """What the field puts on the page: a link, or the text Word rendered.

        Only HYPERLINK is claimed as a link; a PAGEREF or a TOC renders text that
        says nothing about where it came from. A field rendering no text is not on
        the page at all, so it is not made into a link either.
        """
        target = _hyperlink_target(self.instruction)
        text = "".join(run.text for run in self.result)
        if not target or not text.strip():
            return self.result

        return [_Run(text, _link_marks(self.result[0].marks, target))]


_PLAIN = _Marks()


def paragraph_markdown(paragraph: Paragraph) -> str:
    """The paragraph as one block of Markdown, or "" where it says nothing."""
    rendered = (_render_line(line) for line in _paragraph_lines(paragraph))
    return _LINE_BREAK.join(line for line in rendered if line)


def _paragraph_lines(paragraph: Paragraph) -> list[list[_Run]]:
    """The paragraph's runs, split into the lines its soft breaks divide it into.

    Dispatching on the paragraph's own children, rather than on every run beneath
    it, is what keeps a hyperlink whole: w:hyperlink is a sibling of w:r, and the
    runs it holds are one link however many pieces Word split its anchor text into.
    It is also what makes a field legible, a field having no element of its own.

    A field still open when the paragraph ends is rendered anyway. Word does not
    always write the end boundary - one of the two real guides stops a paragraph
    between a field's text and its end - and dropping the field would take the
    words with it.
    """
    lines: list[list[_Run]] = [[]]
    open_field: _Field | None = None

    for child in paragraph._p:
        if child.tag == qn("w:hyperlink"):
            link = _link_run(child, paragraph)
            if link is not None:
                lines[-1].append(link)
        elif child.tag == qn("w:r"):
            open_field = _absorb_run(lines, child, open_field)

    if open_field is not None:
        lines[-1].extend(open_field.rendered())

    return lines


def _absorb_run(
    lines: list[list[_Run]], run: Any, open_field: _Field | None
) -> _Field | None:
    """Take one run into the line being built, or into the field that is open.

    Returns the field still open once this run is taken, so the walk itself holds
    no state of its own.
    """
    boundary = _field_boundary(run)
    if boundary is _FieldBoundary.BEGIN:
        return _Field()
    if open_field is None:
        _append_run(lines, run)
        return None

    if boundary is _FieldBoundary.SEPARATE:
        open_field.showing_result = True
    elif boundary is _FieldBoundary.END:
        lines[-1].extend(open_field.rendered())
        return None
    else:
        open_field.absorb(run)
    return open_field


def _append_run(lines: list[list[_Run]], run: Any) -> None:
    """Add one run's text to the line being built, opening a line at each break."""
    marks = _marks_of(run)
    for index, text in enumerate(_run_texts(run)):
        if index:
            lines.append([])
        lines[-1].append(_Run(text, marks))


def _field_boundary(run: Any) -> _FieldBoundary | None:
    """Which end of a field this run marks, or None for an ordinary run."""
    element = run.find(qn("w:fldChar"))
    if element is None:
        return None
    return _FIELD_BOUNDARIES.get(element.get(qn("w:fldCharType")))


def _instruction_text(run: Any) -> str:
    """The part of a field's instruction this run carries."""
    return "".join(element.text or "" for element in run.iter(qn("w:instrText")))


def _hyperlink_target(instruction: str) -> str:
    """The address a HYPERLINK field points at, or "" for any other field."""
    match = _HYPERLINK_INSTRUCTION.match(instruction.strip())
    return match.group(1) if match else ""


def _run_texts(run: Any) -> list[str]:
    """The run's text, split into one string per soft line break inside it."""
    texts = [""]

    for child in run:
        if child.tag == qn("w:t"):
            texts[-1] += child.text or ""
        elif child.tag == qn("w:tab"):
            texts[-1] += _TAB
        elif child.tag == qn("w:br") and child.get(qn("w:type")) in _SOFT_BREAK_TYPES:
            texts.append("")

    return texts


def _link_run(hyperlink: Any, paragraph: Paragraph) -> _Run | None:
    """The whole of a w:hyperlink as one run, or None where it has no text.

    Marks are taken from the first run: a link's anchor text is uniformly styled in
    practice, so this yields one clean link rather than a fragment per run.
    """
    runs = list(hyperlink.iter(qn("w:r")))
    text = "".join("".join(_run_texts(run)) for run in runs)
    if not text.strip():
        return None

    return _Run(
        text, _link_marks(_marks_of(runs[0]), _link_target(hyperlink, paragraph))
    )


def _link_marks(marks: _Marks, target: str) -> _Marks:
    """The anchor text's own marks, wearing the target.

    Underline and colour are dropped, because Word paints both on every hyperlink by
    style - whatever they were meant to say, the link itself now says it.
    """
    return replace(marks, underline=False, colour="", link=target)


def _link_target(hyperlink: Any, paragraph: Paragraph) -> str:
    """Where a w:hyperlink points: an address outside the file, or a bookmark in it.

    A bookmark is kept exactly as Word wrote it. Resolving one to a section is a
    separate question, and the PoC's attempt at it sent two annex links to the wrong
    section - unresolved beats wrongly resolved.
    """
    relationship_id = hyperlink.get(qn("r:id"))
    if relationship_id in paragraph.part.rels:
        return str(paragraph.part.rels[relationship_id].target_ref)

    anchor = hyperlink.get(qn("w:anchor"))
    return f"#{anchor}" if anchor else ""


def _marks_of(run: Any) -> _Marks:
    """What one run is wearing, read from its run properties."""
    properties = run.find(qn("w:rPr"))
    if properties is None:
        return _PLAIN

    return _Marks(
        bold=is_toggle_on(properties.find(qn("w:b"))),
        italic=is_toggle_on(properties.find(qn("w:i"))),
        underline=_is_underlined(properties.find(qn("w:u"))),
        strikethrough=is_toggle_on(properties.find(qn("w:strike"))),
        vertical=_vertical_tag(properties.find(qn("w:vertAlign"))),
        colour=_colour(properties.find(qn("w:color"))),
    )


def _is_underlined(element: Any) -> bool:
    """Underline is not a toggle: it names a line to draw, and "none" is no line."""
    return element is not None and element.get(qn(W_VAL)) != "none"


def _vertical_tag(element: Any) -> str:
    """The tag for a raised or lowered run, or "" for one sitting on the line."""
    if element is None:
        return ""
    return _VERTICAL_TAGS.get(element.get(qn(W_VAL)), "")


def _colour(element: Any) -> str:
    """The run's colour, or "" where it is only the default one spelled out."""
    if element is None:
        return ""
    value = element.get(qn(W_VAL)) or ""
    return "" if value.upper() in _UNCOLOURED else value


def _render_line(runs: list[_Run]) -> str:
    """One line of a paragraph: its runs merged into spans and marked up."""
    spans = (
        _render_span(marks, "".join(run.text for run in group))
        for marks, group in groupby(runs, key=lambda run: run.marks)
    )
    return "".join(spans).strip()


def _render_span(marks: _Marks, text: str) -> str:
    """Mark up one span, leaving the space around it outside the markers."""
    core = text.strip()
    if not core:
        return text

    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    return f"{leading}{_marked_up(core, marks)}{trailing}"


def _marked_up(text: str, marks: _Marks) -> str:
    """Wrap text in its markers, innermost first."""
    if marks.vertical:
        text = f"<{marks.vertical}>{text}</{marks.vertical}>"
    if marks.strikethrough:
        text = f"~~{text}~~"
    if marks.underline:
        text = f"<u>{text}</u>"
    if marks.italic:
        text = f"*{text}*"
    if marks.bold:
        text = f"**{text}**"
    if marks.colour:
        text = f'<span style="color: #{marks.colour}">{text}</span>'
    if marks.link:
        text = f"[{text}]({_destination(marks.link)})"
    return text


def _destination(target: str) -> str:
    """A link target, wrapped in <> only where bare would not parse."""
    return f"<{target}>" if _NEEDS_BRACKETS.search(target) else target
