"""Turning a paragraph's runs into the Markdown it says.

Word splits a paragraph into runs wherever anything at all changes, and puts the
space between two words inside whichever run it likes, so most of these cases are
about putting a paragraph back together rather than about any one mark.

Each case is parsed through a document with a heading, because content only reaches
the output as a section's content.
"""

import pytest
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement

from app.guidance.parsing import parser

HEADING = "Applying"


def _content(docx_bytes, build) -> str:
    """The Markdown of the one section `build` writes its paragraphs into."""

    def with_heading(document):
        document.add_heading(HEADING, level=1)
        build(document)

    return parser.parse_docx(docx_bytes(with_heading)).sections[0].content


def _colour(run, value: str) -> None:
    """Colour a run by raw w:val, including values python-docx will not write."""
    element = OxmlElement("w:color")
    element.set(qn("w:val"), value)
    run._r.get_or_add_rPr().append(element)


def _hyperlink(paragraph, *texts: str, url: str = "", anchor: str = "") -> list:
    """Append a w:hyperlink holding one run per text, as Word splits anchor text."""
    link = OxmlElement("w:hyperlink")
    if url:
        link.set(
            qn("r:id"), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        )
    if anchor:
        link.set(qn("w:anchor"), anchor)

    runs = [paragraph.add_run(text) for text in texts]
    for run in runs:
        link.append(run._r)
    paragraph._p.append(link)
    return runs


def _break(run, break_type: str | None = None) -> None:
    """Add a w:br to a run, of the given type or of none at all."""
    element = OxmlElement("w:br")
    if break_type is not None:
        element.set(qn("w:type"), break_type)
    run._r.append(element)


class TestPuttingRunsBackTogether:
    def test_runs_wearing_the_same_marks_are_one_span(self, docx_bytes):
        """Word splits a bold phrase at a proofing boundary; it is still one phrase."""

        def build(document):
            paragraph = document.add_paragraph()
            paragraph.add_run("Is ").bold = True
            paragraph.add_run("your claim valid").bold = True

        assert _content(docx_bytes, build) == "**Is your claim valid**"

    def test_a_marker_never_wraps_the_space_word_kept_inside_it(self, docx_bytes):
        """The defect this whole module exists to avoid.

        Word puts the trailing space inside the bold run. `**Note: **read` is not
        emphasis under CommonMark - the asterisks are shown to the reader - and an
        editor that tidies the space outwards deletes it, welding the words together.
        """

        def build(document):
            paragraph = document.add_paragraph()
            paragraph.add_run("Note: ").bold = True
            paragraph.add_run("read the rules before applying.")

        assert (
            _content(docx_bytes, build) == "**Note:** read the rules before applying."
        )

    def test_a_span_of_nothing_but_space_carries_no_markers(self, docx_bytes):
        """Otherwise the space between two bold phrases renders as a literal ****."""

        def build(document):
            paragraph = document.add_paragraph()
            paragraph.add_run("Check")
            paragraph.add_run(" ").bold = True
            paragraph.add_run("again")

        assert _content(docx_bytes, build) == "Check again"

    def test_differently_marked_runs_stay_separate(self, docx_bytes):
        def build(document):
            paragraph = document.add_paragraph()
            paragraph.add_run("Check ").bold = True
            paragraph.add_run("carefully").italic = True

        assert _content(docx_bytes, build) == "**Check** *carefully*"

    def test_each_paragraph_is_its_own_block(self, docx_bytes):
        def build(document):
            document.add_paragraph("Apply before the deadline.")
            document.add_paragraph("Keep a copy of the form.")

        assert _content(docx_bytes, build) == (
            "Apply before the deadline.\n\nKeep a copy of the form."
        )

    def test_a_paragraph_saying_nothing_is_not_a_block(self, docx_bytes):
        """A blank paragraph is spacing, and would otherwise leave a stray gap."""

        def build(document):
            document.add_paragraph("Apply before the deadline.")
            document.add_paragraph("   ")
            document.add_paragraph("Keep a copy of the form.")

        assert _content(docx_bytes, build) == (
            "Apply before the deadline.\n\nKeep a copy of the form."
        )


class TestTheMarksARunCarries:
    @pytest.mark.parametrize(
        ("mark", "expected"),
        [
            pytest.param("bold", "**Overdue**", id="bold"),
            pytest.param("italic", "*Overdue*", id="italic"),
            pytest.param("underline", "<u>Overdue</u>", id="underline"),
            pytest.param("strike", "~~Overdue~~", id="strikethrough"),
            pytest.param("superscript", "<sup>Overdue</sup>", id="superscript"),
            pytest.param("subscript", "<sub>Overdue</sub>", id="subscript"),
        ],
    )
    def test_a_mark_becomes_markdown_or_the_html_markdown_allows(
        self, docx_bytes, mark, expected
    ):
        """Underline, superscript and subscript have no Markdown of their own."""

        def build(document):
            run = document.add_paragraph().add_run("Overdue")
            setattr(run.font, mark, True)

        assert _content(docx_bytes, build) == expected

    @pytest.mark.parametrize(
        "mark",
        [
            pytest.param("bold", id="toggle-set-to-zero"),
            pytest.param("underline", id="line-style-set-to-none"),
        ],
    )
    def test_a_mark_turned_off_is_not_a_mark(self, docx_bytes, mark):
        """Turning a mark off writes it out as off rather than removing it.

        The two are written differently - a toggle takes w:val="0", underline names
        the line "none" - so presence alone would read either as on.
        """

        def build(document):
            run = document.add_paragraph().add_run("Overdue")
            setattr(run.font, mark, False)

        assert _content(docx_bytes, build) == "Overdue"

    def test_marks_nest_in_a_fixed_order(self, docx_bytes):
        def build(document):
            run = document.add_paragraph().add_run("Overdue")
            run.font.bold = True
            run.font.italic = True
            run.font.underline = True

        assert _content(docx_bytes, build) == "***<u>Overdue</u>***"

    def test_a_colour_the_author_reached_for_is_kept(self, docx_bytes):
        """Red marks a mandatory field in both real guides, and says something."""

        def build(document):
            _colour(document.add_paragraph().add_run("Mandatory"), "FF0000")

        assert _content(docx_bytes, build) == (
            '<span style="color: #FF0000">Mandatory</span>'
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("000000", id="black"),
            pytest.param("auto", id="auto"),
        ],
    )
    def test_the_default_text_colour_is_not_a_colour(self, docx_bytes, value):
        """Word spells the default colour out, on four fifths of the coloured runs."""

        def build(document):
            _colour(document.add_paragraph().add_run("Ordinary"), value)

        assert _content(docx_bytes, build) == "Ordinary"


class TestHyperlinks:
    def test_a_link_split_across_runs_is_one_link(self, docx_bytes):
        """w:hyperlink is the unit, however many runs Word broke its text into."""

        def build(document):
            _hyperlink(
                document.add_paragraph(),
                "Guid",
                "ance page",
                url="https://example.org/guidance",
            )

        assert _content(docx_bytes, build) == (
            "[Guidance page](https://example.org/guidance)"
        )

    def test_a_bookmark_is_kept_exactly_as_word_wrote_it(self, docx_bytes):
        """Resolving one to a section is a separate question the PoC got wrong."""

        def build(document):
            _hyperlink(document.add_paragraph(), "Annex A", anchor="_Toc178312")

        assert _content(docx_bytes, build) == "[Annex A](#_Toc178312)"

    def test_a_target_that_would_not_parse_bare_is_bracketed(self, docx_bytes):
        """A space would end the destination, and a bracket would close it early."""

        def build(document):
            _hyperlink(
                document.add_paragraph(), "Claim form", url="https://example.org/a b(1)"
            )

        assert _content(docx_bytes, build) == (
            "[Claim form](<https://example.org/a b(1)>)"
        )

    def test_the_styling_word_paints_on_a_link_is_not_repeated_in_the_text(
        self, docx_bytes
    ):
        """Every hyperlink is underlined and blue by style, so neither says anything."""

        def build(document):
            runs = _hyperlink(
                document.add_paragraph(), "Guidance", url="https://example.org/guidance"
            )
            runs[0].font.underline = True
            _colour(runs[0], "0000FF")

        assert _content(docx_bytes, build) == "[Guidance](https://example.org/guidance)"

    def test_a_mark_of_the_authors_own_survives_a_link(self, docx_bytes):
        def build(document):
            runs = _hyperlink(
                document.add_paragraph(), "Guidance", url="https://example.org/guidance"
            )
            runs[0].font.bold = True

        assert _content(docx_bytes, build) == (
            "[**Guidance**](https://example.org/guidance)"
        )

    @pytest.mark.parametrize(
        "texts",
        [
            pytest.param([], id="no-runs-at-all"),
            pytest.param(["  "], id="nothing-but-space"),
        ],
    )
    def test_a_link_with_no_text_is_left_out(self, docx_bytes, texts):
        """An empty link renders as `[]()`, which is a defect on the page.

        Word writes a run-less w:hyperlink around a bookmark that its text has since
        been deleted from, so there is nothing to take the link's marks from either.
        """

        def build(document):
            paragraph = document.add_paragraph("See the guidance. ")
            _hyperlink(paragraph, *texts, url="https://example.org/guidance")

        assert _content(docx_bytes, build) == "See the guidance."

    def test_a_link_pointing_nowhere_is_left_as_its_text(self, docx_bytes):
        """A w:hyperlink with neither a relationship nor a bookmark has no target."""

        def build(document):
            _hyperlink(document.add_paragraph(), "Guidance")

        assert _content(docx_bytes, build) == "Guidance"


class TestTabsAndBreaks:
    def test_a_tab_becomes_the_separator_it_is(self, docx_bytes):
        """Markdown has no tab stop, and a line opening with one is a code block."""

        def build(document):
            run = document.add_paragraph().add_run()
            run.add_tab()
            run.add_text("Parcel")
            run.add_tab()
            run.add_text("Area")

        assert _content(docx_bytes, build) == "Parcel Area"

    def test_a_soft_break_becomes_a_line_break_in_markdown(self, docx_bytes):
        """A break with no type wraps the line; joining the two loses the wrap."""

        def build(document):
            run = document.add_paragraph().add_run("Example Agency")
            _break(run)
            run.add_text("Claims team")

        assert _content(docx_bytes, build) == "Example Agency\\\nClaims team"

    def test_a_break_with_nothing_after_it_leaves_no_dangling_line(self, docx_bytes):
        """A trailing backslash with no line under it is a defect on the page."""

        def build(document):
            run = document.add_paragraph().add_run("Example Agency")
            _break(run)

        assert _content(docx_bytes, build) == "Example Agency"

    def test_a_page_break_says_nothing_about_the_text_around_it(self, docx_bytes):
        """Pagination is not punctuation: the sentence carries on over the page."""

        def build(document):
            paragraph = document.add_paragraph()
            run = paragraph.add_run("Apply before ")
            _break(run, "page")
            paragraph.add_run("the deadline.")

        assert _content(docx_bytes, build) == "Apply before the deadline."
