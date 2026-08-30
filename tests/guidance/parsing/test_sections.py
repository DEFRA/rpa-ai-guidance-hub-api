"""Turning heading paragraphs into numbered sections.

Word does not put the number in the text. Both real guides attach the numbering to
the heading *styles*, so Word generates "4.3.1.1" as it renders the page and the
paragraph itself says only "Split". Every number here is therefore derived from the
heading structure alone, and these cases pin down how.

Levels are read as relative, so most of the interesting cases are documents whose
headings do not descend one tidy level at a time.
"""

import pytest

from app.guidance.parsing import parser


def _outline(source: bytes) -> list[tuple[str, str]]:
    """The parsed sections as (number, heading) pairs, in document order."""
    return [
        (section.number, section.heading)
        for section in parser.parse_docx(source).sections
    ]


class TestBuildingTheSectionTree:
    def test_headings_nest_by_level(self, docx_bytes):
        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("Eligibility", level=2)
            document.add_heading("Land parcels", level=3)

        assert _outline(docx_bytes(build)) == [
            ("1", "Applying"),
            ("1.1", "Eligibility"),
            ("1.1.1", "Land parcels"),
        ]

    def test_each_parent_counts_its_own_children(self, docx_bytes):
        """The second parent's children restart at 1 rather than carrying on."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("Eligibility", level=2)
            document.add_heading("Evidence", level=2)
            document.add_heading("Assessing", level=1)
            document.add_heading("Checks", level=2)

        assert _outline(docx_bytes(build)) == [
            ("1", "Applying"),
            ("1.1", "Eligibility"),
            ("1.2", "Evidence"),
            ("2", "Assessing"),
            ("2.1", "Checks"),
        ]

    def test_returning_to_a_shallower_level_resumes_the_outer_count(self, docx_bytes):
        """Closing two levels at once must not lose the top-level count."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("Eligibility", level=2)
            document.add_heading("Land parcels", level=3)
            document.add_heading("Assessing", level=1)

        assert _outline(docx_bytes(build))[-1] == ("2", "Assessing")

    def test_a_document_opening_at_heading_2_still_starts_at_one(self, docx_bytes):
        """Level is relative to the document, not to Word's numbering."""

        def build(document):
            document.add_heading("Applying", level=2)
            document.add_heading("Assessing", level=2)

        assert _outline(docx_bytes(build)) == [
            ("1", "Applying"),
            ("2", "Assessing"),
        ]

    def test_a_skipped_level_nests_one_deep(self, docx_bytes):
        """A jump from 1 to 3 is a child, not a gap: there is no "1.0.1"."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("Land parcels", level=3)
            document.add_heading("Evidence", level=3)

        assert _outline(docx_bytes(build)) == [
            ("1", "Applying"),
            ("1.1", "Land parcels"),
            ("1.2", "Evidence"),
        ]


class TestWhatDoesNotOpenASection:
    def test_text_before_the_first_heading_opens_no_section(self, docx_bytes):
        def build(document):
            document.add_paragraph("This guide covers the whole claim.")
            document.add_heading("Applying", level=1)

        assert _outline(docx_bytes(build)) == [("1", "Applying")]

    def test_a_heading_with_no_text_is_not_a_section(self, docx_bytes):
        """An empty heading is spacing. Numbering it invents a section."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("   ", level=1)
            document.add_heading("Assessing", level=1)

        assert _outline(docx_bytes(build)) == [
            ("1", "Applying"),
            ("2", "Assessing"),
        ]

    @pytest.mark.parametrize(
        "style_name",
        [
            pytest.param("Heading Box", id="no-level"),
            pytest.param("Heading 2 Box", id="level-then-more-name"),
            pytest.param("Box Heading 2", id="more-name-then-level"),
            pytest.param("Heading 0", id="level-zero"),
        ],
    )
    def test_a_style_only_named_like_a_heading_is_body_text(
        self, docx_bytes, in_style, style_name
    ):
        """A heading style is named for the word and a level from 1, and nothing else.

        Templates carry plenty of styles built around the word - a heading for a
        table, a heading for an annex - and none of them is a body heading. Reading
        a level out of one anyway would also let a "Heading 0" outrank the document
        itself, leaving a section with nothing to hang from.
        """

        def build(document):
            document.add_heading("Applying", level=1)
            in_style(document, "Deadline reminder", style_name)

        assert _outline(docx_bytes(build)) == [("1", "Applying")]

    def test_a_document_with_no_headings_has_no_sections(self, docx_bytes):
        def build(document):
            document.add_paragraph("This guide covers the whole claim.")

        assert _outline(docx_bytes(build)) == []


class TestWhereContentGoes:
    def test_a_paragraph_belongs_to_the_section_opened_most_recently(self, docx_bytes):
        """Whatever its depth: prose under 1.1 is 1.1's, not section 1's."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_paragraph("Read this section first.")
            document.add_heading("Eligibility", level=2)
            document.add_paragraph("Apply before the deadline.")

        sections = parser.parse_docx(docx_bytes(build)).sections

        assert [section.content for section in sections] == [
            "Read this section first.",
            "Apply before the deadline.",
        ]

    def test_nothing_ahead_of_the_first_heading_becomes_content(self, docx_bytes):
        """In both real guides everything there is the cover page and the contents.

        25 and 30 blocks of it, and not one line of body prose - so it is left out
        rather than filed under a section it does not belong to.
        """

        def build(document):
            document.add_paragraph("Example Grant Scheme Guide", style="Title")
            document.add_paragraph("Printed on recycled paper.")
            document.add_heading("Applying", level=1)
            document.add_paragraph("Apply before the deadline.")

        document = parser.parse_docx(docx_bytes(build))

        assert document.sections[0].content == "Apply before the deadline."
        assert "recycled" not in document.markdown()

    def test_a_contents_entry_after_a_heading_is_still_not_content(
        self, docx_bytes, in_style
    ):
        """Word regenerates a contents page, so its entries are never prose.

        They sit ahead of every heading in both real guides and so never reach the
        content walk at all, but a contents page opening with a heading of its own
        would otherwise file all 23 entries as that section's text.
        """

        def build(document):
            document.add_heading("Contents", level=1)
            in_style(document, "1 Applying ....... 3", "TOC 1")
            document.add_heading("Applying", level=1)
            document.add_paragraph("Apply before the deadline.")

        sections = parser.parse_docx(docx_bytes(build)).sections

        assert [section.content for section in sections] == [
            "",
            "Apply before the deadline.",
        ]


class TestRenderingTheSections:
    def test_a_section_is_headed_by_its_number_at_its_own_depth(self, docx_bytes):
        """The hash count follows the parent chain, so a skipped level stays sane."""

        def build(document):
            document.add_heading("Applying", level=1)
            document.add_heading("Land parcels", level=3)

        markdown = parser.parse_docx(docx_bytes(build)).markdown()

        assert "## 1 Applying" in markdown
        assert "### 1.1 Land parcels" in markdown
