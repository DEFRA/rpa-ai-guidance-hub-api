"""What the conversion audit reads off the Word side of a document.

The audit is the oracle, so a mark it credits to the page that Word never draws is
not reported as a fault of its own: it is reported as a mark the parser lost, and
the search for it starts in the parser. These cases are about the Word side saying
what Word says and no more.

All fixture text is invented, as everywhere in the suite.
"""

from collections import Counter
from typing import Any

import audit_docx
import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement
from docx.text.paragraph import Paragraph


def _text_box(*runs: tuple[str, str]) -> Any:
    """A w:txbxContent holding one paragraph of runs, each with a colour or "" ."""
    box = OxmlElement("w:txbxContent")
    paragraph = OxmlElement("w:p")
    for text, colour in runs:
        run = OxmlElement("w:r")
        if colour:
            run.append(_colour(colour))
        element = OxmlElement("w:t")
        element.text = text
        run.append(element)
        paragraph.append(run)
    box.append(paragraph)
    return box


def _colour(value: str) -> Any:
    """The run properties that paint a run one colour."""
    properties = OxmlElement("w:rPr")
    element = OxmlElement("w:color")
    element.set(qn("w:val"), value)
    properties.append(element)
    return properties


def _in_style(document: Any, text: str, style_name: str) -> None:
    """Add a paragraph in a named style, defining the style if the template lacks it.

    python-docx will only apply a style the template already knows, and the styles
    worth testing against here - the contents styles a guide actually carries - are
    exactly the ones it does not have.
    """
    if all(style.name != style_name for style in document.styles):
        document.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph(text, style=style_name)


def _bulleted(paragraph: Paragraph) -> None:
    """Put numbering on a paragraph, which is the whole of what makes it an item.

    The list itself is left undeclared: with no numbering part to read a format
    from, a bullet is what the audit reads, which is what Word draws when it has
    nothing else to draw.
    """
    numbering = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    numbering.get_or_add_numId().set(qn("w:val"), "1")


def _anchor(paragraph: Paragraph, box: Any, colour: str = "") -> None:
    """Hang a text box off a run of the paragraph, the way a drawing does.

    Only the nesting matters to the walk, not the shape around it, so the w:drawing
    is written without the DrawingML that would tell Word how big to draw it.
    """
    run = paragraph.add_run()
    if colour:
        run._r.append(_colour(colour))
    drawing = OxmlElement("w:drawing")
    drawing.append(box)
    run._r.append(drawing)


class TestTextBoxMarks:
    def test_a_box_does_not_wear_the_marks_of_the_run_anchoring_it(self):
        """Word colours the anchor character, not the story the box holds.

        A real guide anchors a case note off a red run, and every word of the note
        is then read as red here while the page shows two of them that way. The
        parser marks up what the page shows, so the difference is charged to it: one
        section of that guide loses a seventh of its marks to a loss nobody made.
        """
        document = docx.Document()
        paragraph = document.add_paragraph()
        _anchor(
            paragraph,
            _text_box(("Case closed. ", ""), ("<input the date>", "FF0000")),
            colour="FF0000",
        )

        bag = audit_docx.Bag()
        audit_docx.mark_paragraph(bag, paragraph, frozenset())

        assert audit_docx.marks_of(bag.marks, audit_docx.RED) == Counter(
            {"input": 1, "the": 1, "date": 1}
        )

    def test_a_box_is_not_an_item_of_the_list_its_anchor_is_in(self):
        """A bulleted paragraph draws one bullet, and none on the box it anchors.

        A real guide bullets "Update the case note ... following the below template."
        and hangs the case note itself off that paragraph as a text box. Word draws
        no bullet on the box: its paragraphs carry no numbering of their own, and the
        same box drawn as a one-cell table - the same box, by this file's own
        reckoning of what a box is - is credited with no list at all. Inherited, the
        anchor's bullet charges the parser with seventy list marks that section never
        drew, in the one section of that guide scoring below 100%.
        """
        document = docx.Document()
        paragraph = document.add_paragraph("Update the case note.")
        _bulleted(paragraph)
        _anchor(paragraph, _text_box(("Example: Parcel ABC 1234.", "")))

        section = audit_docx.Section("Amending the agreement")
        audit_docx.absorb(section, paragraph)

        assert audit_docx.marks_of(section.bag.marks, audit_docx.LIST) == Counter(
            {"update": 1, "the": 1, "case": 1, "note": 1}
        )
        assert audit_docx.marks_of(section.bag.marks, audit_docx.BOX) == Counter(
            {"example": 1, "parcel": 1, "abc": 1, "1234": 1}
        )


class TestContentsEntries:
    def test_a_stray_contents_entry_does_not_end_the_section(self):
        """A contents entry is navigation wherever it turns up, and no more than that.

        One guide carries an empty paragraph styled "Contents RPA" in the middle of a
        section. Ending the section there drops every paragraph after it up to the
        next heading - eighty-five words the page really shows, and every mark on
        them - so nothing is reported missing, because the Word side never claimed
        them, and the parser is charged instead with sixty-five marks it invented.
        An oracle that under-counts accuses; it does not report itself.

        Across all nineteen guides only this one paragraph carries a contents style
        after the first body heading. Every real contents entry sits ahead of it,
        where there is no open section to end, so closing one here was never the
        rule doing its job. `parser._body_paragraph` states the rule the other way
        round and is right: contents entries are left out wherever they turn up.
        """
        document = docx.Document()
        document.add_heading("Applying", level=1)
        document.add_paragraph("Before the entry.")
        _in_style(document, "", "Contents Entry")
        document.add_paragraph("After the entry.")

        sections = audit_docx.word_sections(document)

        assert [section.heading for section in sections] == ["Applying"]
        assert sections[0].bag.words == Counter(
            {"applying": 1, "before": 1, "the": 2, "entry": 2, "after": 1}
        )
