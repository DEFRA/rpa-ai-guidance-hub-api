"""Turn a Word paragraph into the Markdown it says.

Word does not store a paragraph as text with formatting laid over it. It stores a
sequence of runs, and starts a new one wherever anything at all changes - a proofing
boundary, an edit, a language mark - so "Is your claim valid" arrives as two bold
runs, "Is " and "your claim valid". Putting a paragraph back together is this
module's work, and two rules do most of it:

- Adjacent runs wearing the same marks are one span. A marker per run breaks one
  emphasised phrase into several, and where two of them meet, into "****".
- A span's markers never wrap the space around it. Word keeps the trailing space
  inside the bold run, and "**Note: **Validation" is not emphasis under CommonMark
  at all - the reader is shown the asterisks - while an editor that tidies the space
  outwards deletes it and welds "Note:" to the word after. Hoisting the space out
  here is what stops both.

Bold, italic and strikethrough are written as Markdown. Underline, superscript and
subscript have no Markdown, so they are written as the HTML that Markdown allows.

Colour has no Markdown either and is deliberately *not* written as HTML - `colours`
owns both what it means and how it is spelled. It is kept only where it says
something: Word writes the default text colour out explicitly rather than leaving it
unsaid, and paints its own blue on every hyperlink, which the output already renders
as a link. What is left is a colour the author reached for.

A link is not always an element. Word's older HYPERLINK field writes the address as
an instruction between field boundaries, with the text rendered from it following,
so a parser reading only w:hyperlink shows their text with no link on it.

Text the author typed is escaped, so that text which happens to look like syntax is
read as text. Word says nothing about the difference: "<CS Claim Revenue>" is a
placeholder for a reader and a tag to a renderer, and "[SBI]*[Title]*" loses its
asterisks to emphasis. The escape is unconditional rather than asking which asterisk
would have opened emphasis - that question has a different answer in every position,
while over-escaping renders identically - and it is character-for-character the
function the editor applies, so what the parser writes is already what the editor
would save.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import groupby
from typing import TYPE_CHECKING, Any

from docx.oxml.ns import qn

from app.guidance.parsing import colours, images
from app.guidance.parsing.ooxml import W_VAL, is_toggle_on

if TYPE_CHECKING:
    from docx.text.paragraph import Paragraph

# Markdown has no tab stop, and a line that opens with one is a code block, so a tab
# is spent as the separator it is.
_TAB = " "

# CommonMark's hard line break: a backslash ending the line.
_LINE_BREAK = "\\\n"

# A break carrying no type is a soft one, which is also what "textWrapping" spells.
# The rest - page, column - are pagination, and say nothing about the text.
_SOFT_BREAK_TYPES = (None, "textWrapping")

_VERTICAL_TAGS = {"superscript": "sup", "subscript": "sub"}

# Whitespace ends a bare Markdown link destination, so a target carrying any has to
# be wrapped in <>. Parentheses do not: CommonMark allows them bare while they
# balance, which the addresses that carry them - a filename in a path - always do.
_HAS_WHITESPACE = re.compile(r"\s")

# A link field's instruction, e.g. `HYPERLINK "https://example.org/a"`. Word may split
# it across several instrText runs, so it is matched once they are reassembled.
_HYPERLINK_INSTRUCTION = re.compile(r'HYPERLINK\s+"([^"]*)"', re.IGNORECASE)

# What the editor's serialiser does to a text node, taken from it exactly: first
# encodeHtmlEntities, then escapeMarkdownSyntax. The ampersand is replaced *first*
# and this is load-bearing - the other way round, "<" would become "&amp;lt;".
_HTML_ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
_MARKDOWN_SYNTAX = re.compile(r"([\\`*_\[\]~])")


class _FieldBoundary(StrEnum):
    """The three positions a fldChar marks, spelled as Word spells them."""

    BEGIN = "begin"
    SEPARATE = "separate"
    END = "end"


# What Word writes is a value, not a member, and this turns one into the other -
# through .get(), so a value that is no boundary at all needs no branch of its own.
# Built from __members__ because an enum class is iterable only through its
# metaclass, which static analysis routinely fails to see; it is the same sequence.
_FIELD_BOUNDARIES = {
    boundary.value: boundary for boundary in _FieldBoundary.__members__.values()
}


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
    """One piece of a line: a stretch of text and its marks, or a picture.

    A picture is not text. It carries no marks - Word will happily leave bold or a
    colour on the run it draws a picture in, which is formatting reaching a place
    where it means nothing - and it is never escaped, since the `![]()` is markup
    this parser generated rather than anything the author typed.
    """

    text: str
    marks: _Marks
    image: str = ""


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

    def absorb_link(self, link: _Run) -> None:
        """Take a w:hyperlink written inside the field as part of its result.

        A field's instruction is instrText and never an element, so a w:hyperlink
        met while a field is open is always something Word rendered - and it has to
        go in here rather than straight onto the line, or it is emitted ahead of the
        whole field and the end of its anchor text welds to the start of the text
        the field renders after it.
        """
        self.result.append(link)

    def rendered(self) -> list[_Run]:
        """What the field puts on the page: a link, or the text Word rendered.

        Only HYPERLINK is claimed as a link; a PAGEREF or a TOC renders text that
        says nothing about where it came from. A field rendering no text is not on
        the page at all, so it is not made into a link either.

        A w:hyperlink nested in the result keeps the address it carries: it is the
        nearer of the two over the text it covers, and the one Word follows. The
        text either side of it is still the field's, so what comes back is one run
        per stretch rather than one run for the lot.
        """
        target = _hyperlink_target(self.instruction)
        if not target:
            return self.result

        rendered: list[_Run] = []
        for linked, group in groupby(self.result, key=_wears_a_link):
            runs = list(group)
            text = "".join(run.text for run in runs)
            if linked or not text.strip():
                rendered.extend(runs)
            else:
                rendered.append(_Run(text, _link_marks(runs[0].marks, target)))
        return rendered


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
    It is also what makes a field legible, a field having no element of its own -
    and why a hyperlink met inside an open field is handed to the field: the two are
    siblings, so only the walk knows that one is inside the other.

    A field still open when the paragraph ends is rendered anyway. Word does not
    always write the end boundary, and dropping an unterminated field would take its
    words with it.
    """
    lines: list[list[_Run]] = [[]]
    open_field: _Field | None = None

    for child in paragraph._p:
        if child.tag == qn("w:hyperlink"):
            link = _link_run(child, paragraph)
            if link is None:
                continue
            if open_field is None:
                lines[-1].append(link)
            else:
                open_field.absorb_link(link)
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
    """Add one run's content to the line being built, opening a line at each break.

    Word gives a picture a run of its own, holding the drawing and no text, no tab
    and no break, so the picture is the whole of what such a run has to say.
    """
    marks = _marks_of(run)
    embed = images.embedded_in(run)
    if embed:
        lines[-1].append(_Run("", marks, image=embed))
        return

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


def _wears_a_link(run: _Run) -> bool:
    """Whether this run already carries an address of its own."""
    return bool(run.marks.link)


def _link_marks(marks: _Marks, target: str) -> _Marks:
    """The anchor text's own marks, wearing the target.

    Underline and colour are dropped, because Word paints both on every hyperlink by
    style - whatever they were meant to say, the link itself now says it.
    """
    return replace(marks, underline=False, colour="", link=target)


def _link_target(hyperlink: Any, paragraph: Paragraph) -> str:
    """Where a w:hyperlink points: an address outside the file, or a bookmark in it.

    A bookmark is kept exactly as Word wrote it. Resolving one to a section is a
    separate question, answered where the whole document is known - and a name left
    raw beats a link sent confidently to the wrong section.
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
    """The name of the run's colour, or "" where it wears none worth carrying.

    A name rather than the hex Word wrote, so that marks comparing equal are runs an
    author meant alike: two shades of the same intent merge into one span instead of
    breaking a phrase in half at a colour boundary nobody can see.
    """
    return "" if element is None else colours.name_for(element.get(qn(W_VAL)))


def _render_line(runs: list[_Run]) -> str:
    """One line of a paragraph: its runs merged into spans and marked up.

    Runs merge only where they are text and their marks agree, because Word splits a
    word across runs wherever anything at all changes. A picture merges with nothing
    and takes the marks of nothing: two pictures side by side are two pictures.
    """
    parts: list[str] = []
    for (marks, is_image), group in groupby(runs, key=_span_key):
        pieces = list(group)
        if is_image:
            parts.extend(images.placeholder(piece.image) for piece in pieces)
            continue

        parts.append(_render_span(marks, "".join(piece.text for piece in pieces)))

    return "".join(parts).strip()


def _span_key(run: _Run) -> tuple[_Marks, bool]:
    """What decides whether two neighbouring runs render as one span."""
    return run.marks, bool(run.image)


def _render_span(marks: _Marks, text: str) -> str:
    """Mark up one span, leaving the space around it outside the markers."""
    core = text.strip()
    if not core:
        return text

    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    return f"{leading}{_marked_up(_escaped(core), marks)}{trailing}"


def _escaped(text: str) -> str:
    """Document text, written so that it says itself and not syntax.

    This is the only place the parser sees text the author typed rather than markup
    it generated itself, which is what lets the rule be unconditional: the markers,
    the brackets around a link and its destination are all added after this point,
    and the space around the span was hoisted out before it.
    """
    for character, entity in _HTML_ENTITIES:
        text = text.replace(character, entity)
    return _MARKDOWN_SYNTAX.sub(r"\\\1", text)


def _marked_up(text: str, marks: _Marks) -> str:
    """Wrap text in its markers, innermost first.

    Colour goes on last of the marks, so that what a coloured span holds is the
    marked-up Markdown rather than the bare text: the editor re-tokenises a span's
    contents, so emphasis inside one is emphasis again when it is read back.
    """
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
        text = colours.marked_up(text, marks.colour)
    if marks.link:
        text = f"[{text}]({_destination(marks.link)})"
    return text


def _destination(target: str) -> str:
    """A link target, wrapped in <> only where bare would not parse.

    Wrapping every target holding a parenthesis would be the easier rule and is the
    wrong one, because the editor writes a balanced target bare: a document written
    the cautious way is rewritten by the first save, and a table holding such a link
    is re-measured and re-padded whole. Asking the real question - would this be
    misread bare? - agrees with the editor without conceding anything, since the
    targets it declines to wrap are exactly the ones that do not need it.
    """
    return f"<{target}>" if _needs_brackets(target) else target


def _needs_brackets(target: str) -> bool:
    """Whether a bare destination would be read as something other than itself."""
    if _HAS_WHITESPACE.search(target):
        return True

    depth = 0
    for character in target:
        depth += (character == "(") - (character == ")")
        if depth < 0:
            # A close before its open ends the destination early, taking the rest
            # of the address out of the link with it.
            return True
    return depth != 0
