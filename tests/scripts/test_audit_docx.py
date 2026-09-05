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
