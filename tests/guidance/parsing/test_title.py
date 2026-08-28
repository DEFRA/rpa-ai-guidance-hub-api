"""Inferring the document's title.

The cover page is read first and the docProps title is only a fallback, so most of
these cases are about reconstructing what the cover actually printed, and about
knowing where the cover stops.
"""

from docx.enum.text import WD_BREAK

from app.guidance.parsing import parser

TITLE = "Example Grant Scheme Guide"
LONG_TITLE = "Example Grant Scheme Processing to Final Payment Guide"


def _page_break(document) -> None:
    """Add the paragraph Word inserts for an explicit page break."""
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


class TestReconstructingTheCoverTitle:
    def test_the_printed_cover_title_is_used(self, docx_bytes):
        def build(document):
            document.add_paragraph(TITLE, style="Title")
            _page_break(document)
            document.add_paragraph("1 Introduction", style="Heading 1")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_title_styled_paragraphs_win_over_other_cover_text(self, docx_bytes):
        def build(document):
            document.add_paragraph("Example Agency")
            document.add_paragraph("")
            document.add_paragraph(TITLE, style="Title")
            _page_break(document)

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_hand_wrapped_lines_are_rejoined_into_one_part(self, docx_bytes):
        """Consecutive lines are one title the author wrapped, not two parts."""

        def build(document):
            document.add_paragraph("Example Grant Scheme Processing", style="Title")
            document.add_paragraph("to Final Payment Guide", style="Title")
            _page_break(document)

        assert parser.parse_docx(docx_bytes(build)).title == LONG_TITLE

    def test_a_blank_line_separates_two_parts_of_a_title(self, docx_bytes):
        def build(document):
            document.add_paragraph(TITLE, style="Title")
            document.add_paragraph("")
            document.add_paragraph("2026 edition", style="Title")
            _page_break(document)

        assert parser.parse_docx(docx_bytes(build)).title == f"{TITLE} — 2026 edition"

    def test_padding_blank_paragraphs_do_not_form_parts_of_their_own(self, docx_bytes):
        def build(document):
            document.add_paragraph("")
            document.add_paragraph("")
            document.add_paragraph(TITLE, style="Title")
            document.add_paragraph("")
            _page_break(document)

        assert parser.parse_docx(docx_bytes(build)).title == TITLE


class TestWhereTheCoverEnds:
    def test_a_page_break_ends_the_cover(self, docx_bytes):
        def build(document):
            document.add_paragraph(TITLE, style="Title")
            _page_break(document)
            document.add_paragraph("Not part of the title")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_a_paragraph_carrying_a_trailing_break_is_still_cover_text(
        self, docx_bytes
    ):
        """The break ends the page *after* this paragraph, so its words are cover."""

        def build(document):
            paragraph = document.add_paragraph(TITLE, style="Title")
            paragraph.add_run().add_break(WD_BREAK.PAGE)
            document.add_paragraph("Not part of the title")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_a_paragraph_forced_onto_a_new_page_is_not_cover_text(self, docx_bytes):
        """pageBreakBefore means the opposite: this paragraph is already overleaf."""

        def build(document):
            document.add_paragraph(TITLE, style="Title")
            overleaf = document.add_paragraph("Not part of the title")
            overleaf.paragraph_format.page_break_before = True

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_a_table_of_contents_ends_the_cover(self, docx_bytes):
        def build(document):
            document.add_paragraph(TITLE, style="Title")
            document.add_paragraph("Contents", style="TOC Heading")
            document.add_paragraph("1 Introduction ....... 3")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE

    def test_a_body_heading_ends_the_cover_with_no_break_at_all(self, docx_bytes):
        """Without this a coverless document swallows its opening heading."""

        def build(document):
            document.add_paragraph(TITLE, style="Title")
            document.add_paragraph("1 Introduction", style="Heading 1")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE


class TestFallingBackToStoredProperties:
    def test_core_properties_are_used_when_there_is_no_cover(self, docx_bytes):
        def build(document):
            document.add_paragraph("1 Introduction", style="Heading 1")

        document = parser.parse_docx(docx_bytes(build, core_title="Stored Title"))

        assert document.title == "Stored Title"

    def test_the_printed_cover_beats_stale_core_properties(self, docx_bytes):
        """The real case: properties carried forward from the file this was copied from.

        Core properties said "2024", the cover printed no year, and the file was the
        2026 edition. Metadata goes stale silently; the cover is what a reader sees.
        """

        def build(document):
            document.add_paragraph(LONG_TITLE, style="Title")
            _page_break(document)

        document = parser.parse_docx(docx_bytes(build, core_title=f"2024 {LONG_TITLE}"))

        assert document.title == LONG_TITLE

    def test_a_document_with_neither_has_an_empty_title(self, docx_bytes):
        def build(document):
            document.add_paragraph("1 Introduction", style="Heading 1")

        assert parser.parse_docx(docx_bytes(build)).title == ""

    def test_a_template_name_is_no_longer_special_cased(self, docx_bytes):
        """The PoC ignored one hardcoded template title so the cover could win.

        Reading the cover first makes that workaround unnecessary; where there is no
        cover at all, the stored name is the only title the document has.
        """

        def build(document):
            document.add_paragraph("1 Introduction", style="Heading 1")

        document = parser.parse_docx(
            docx_bytes(build, core_title="Guidance Document Template")
        )

        assert document.title == "Guidance Document Template"
