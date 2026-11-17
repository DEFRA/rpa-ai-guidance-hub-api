"""Reading a stored document back into the model that wrote it.

These cases are the whole warrant for storing one plain Markdown file rather than a
file and a description of itself: everything the model holds has to be recoverable
from what the document prints, or the stored form is lossy and nobody finds out until
a guide is edited.

The round trip is written as `parse -> render -> read`, never as a model built by hand
and read back, because it is the rendered form that has to be understood - a fixture
written to suit the reader would prove only that the reader agrees with itself.

All fixture text is invented, as everywhere in this package.
"""

from app.guidance.documents import reader
from app.guidance.parsing import models

PREFIX = "s3://assets/01JBQ8/"


def _round_trip(document: models.MarkdownDocument) -> models.MarkdownDocument:
    """The document read back out of its own rendering."""
    return reader.from_markdown(document.markdown(PREFIX))


def _document(
    *sections: models.MarkdownSection, title: str = "Claims"
) -> models.MarkdownDocument:
    return models.MarkdownDocument(title=title, sections=list(sections))


class TestWhatIsPrinted:
    def test_the_title_comes_back(self):
        assert _round_trip(_document()).title == "Claims"

    def test_a_document_naming_no_title_reads_back_as_naming_none(self):
        """The cover of a real guide sometimes names nothing, and `# ` is what that
        renders as - a heading line with an empty heading."""
        assert _round_trip(_document(title="")).title == ""

    def test_a_line_of_content_beginning_with_a_hash_is_not_the_title(self):
        """The title is read positionally, off the first line, so content cannot
        impersonate it."""
        section = models.MarkdownSection(heading="Applying", content="#1 on the list")

        assert _round_trip(_document(section)).title == "Claims"

    def test_a_section_with_no_content_comes_back_with_none(self):
        section = models.MarkdownSection(heading="Applying")

        assert _round_trip(_document(section)).sections[0].content == ""


class TestHierarchy:
    def test_depth_is_read_off_the_hashes(self):
        parent = models.MarkdownSection(heading="Eligibility")
        child = models.MarkdownSection(heading="Evidence", parent=parent)
        parent.children.append(child)

        read = _round_trip(_document(parent, child)).sections

        assert [section.level for section in read] == [1, 2]
        assert read[1].parent is read[0]
        assert read[0].children == [read[1]]

    def test_a_number_is_derived_again_rather_than_read(self):
        """Nothing stores a number: it is the parent chain and the ordinal, which is
        what lets a moved section renumber."""
        first = models.MarkdownSection(heading="Applying", ordinal=1)
        parent = models.MarkdownSection(heading="Eligibility", ordinal=2)
        child = models.MarkdownSection(heading="Evidence", ordinal=1, parent=parent)
        parent.children.append(child)

        read = _round_trip(_document(first, parent, child)).sections

        assert [section.number for section in read] == ["1", "2", "2.1"]

    def test_an_ordinal_out_of_step_with_its_siblings_does_not_survive(self):
        """`ordinal` is documented as a section's position among its siblings, and
        the reader holds it to that: it recognises a numbered heading by
        regenerating the number that position prints and finding it on the page.

        A model whose only top-level section is numbered 3 is already inconsistent -
        it renders a document whose first heading is "3" - and this states what
        becomes of one rather than leaving it to be met in the wild. Anything the
        parser or the reader builds counts its ordinals up from 1, so neither
        produces this.
        """
        lone = models.MarkdownSection(heading="Eligibility", ordinal=3)

        read = _round_trip(_document(lone)).sections[0]

        assert read.heading == "3 Eligibility"
        assert read.appendix is True


class TestOrdinalsAndAppendices:
    def test_an_annex_between_two_numbered_sections_breaks_neither_run(self):
        """Appendices and numbered sections are counted apart, so an annex after
        section 1 is A and the numbered heading after the annex is 2. Reading the
        counts back the way the parser keeps them is the whole of how an ordinal is
        recovered."""
        first = models.MarkdownSection(heading="Applying", ordinal=1)
        annex = models.MarkdownSection(
            heading="Annex A - Case types", ordinal=1, appendix=True
        )
        second = models.MarkdownSection(heading="Payment", ordinal=2)

        read = _round_trip(_document(first, annex, second)).sections

        assert [section.number for section in read] == ["1", "A", "2"]
        assert [section.appendix for section in read] == [False, True, False]

    def test_an_appendix_headed_with_a_number_is_still_an_appendix(self):
        """The reader asks whether a heading starts with *its own* number, not
        whether it starts with a number. Parsing any leading digits would read this
        annex as section 1 and its heading as "rates"."""
        annex = models.MarkdownSection(heading="2024 rates", ordinal=1, appendix=True)

        read = _round_trip(_document(annex)).sections[0]

        assert read.appendix is True
        assert read.heading == "2024 rates"

    def test_a_heading_whose_own_text_begins_with_a_number_keeps_it(self):
        """ "1 2024 rates" is section 1 headed "2024 rates", and the number stripped
        is the one the section printed rather than the first one on the line."""
        section = models.MarkdownSection(heading="2024 rates", ordinal=1)

        assert _round_trip(_document(section)).sections[0].heading == "2024 rates"


class TestImages:
    def test_the_prefix_comes_off_and_the_bare_name_comes_back(self):
        section = models.MarkdownSection(
            heading="Evidence",
            content="![](a3f9.png)",
            images=[models.Image(name="a3f9.png", content_type="image/png")],
        )

        read = _round_trip(_document(section)).sections[0]

        assert read.content == "![](a3f9.png)"
        assert [image.name for image in read.images] == ["a3f9.png"]

    def test_a_picture_comes_back_without_its_bytes(self):
        """They are in the asset store under this name, and a reader that invented
        empty bytes would be claiming to hold a picture it does not have."""
        section = models.MarkdownSection(
            heading="Evidence",
            content="![](a3f9.png)",
            images=[models.Image(name="a3f9.png", content_type="image/png")],
        )

        assert _round_trip(_document(section)).sections[0].images[0].data is None

    def test_the_type_is_read_from_the_extension_the_name_carries(self):
        section = models.MarkdownSection(
            heading="Evidence",
            content="![](b7c2.jpeg)",
            images=[models.Image(name="b7c2.jpeg", content_type="image/jpeg")],
        )

        read = _round_trip(_document(section)).sections[0]

        assert read.images[0].content_type == "image/jpeg"

    def test_a_link_is_not_a_picture(self):
        """Told apart by the `!`, not by what the target starts with - an empty
        prefix would otherwise make every link in the document an image."""
        section = models.MarkdownSection(
            heading="Applying", content="See [the form](https://example.org/form)"
        )

        assert _round_trip(_document(section)).sections[0].images == []


class TestTables:
    def test_a_table_comes_back_in_the_shape_the_file_is_in(self):
        """`aligned` pads every cell out to its column at render time, and the
        reader keeps that padding rather than undoing it.

        Harmless, because `aligned` strips a cell before measuring it: the padding
        read back here is thrown away and recomputed on the next render, so what is
        stored does not drift. `test_writing_what_was_read_stores_the_same_bytes`
        is the case that holds that down.
        """
        content = "| Case | Action |\n| --- | --- |\n| Withdrawal | Reject |"
        section = models.MarkdownSection(heading="Applying", content=content)

        read = _round_trip(_document(section)).sections[0]

        assert (
            read.content
            == "| Case       | Action |\n| ---------- | ------ |\n| Withdrawal | Reject |"
        )

    def test_writing_what_was_read_stores_the_same_bytes(self):
        """The guarantee the stored form actually offers, and the reason the reader
        need not undo the padding: a guide written, read and written again is byte
        for byte the guide it was."""
        content = "| Case | Amount |\n| --- | --- |\n| A much longer case | 100000 |"
        document = _document(models.MarkdownSection(heading="Rates", content=content))

        first = document.markdown(PREFIX)
        second = reader.from_markdown(first).markdown(PREFIX)
        third = reader.from_markdown(second).markdown(PREFIX)

        assert first == second == third

    def test_prose_that_begins_with_a_pipe_is_left_alone(self):
        """A row alone is not a table - only the delimiter under it makes one - and
        the reader has to draw that line in the same place the writer did."""
        content = "| not a table, just prose"
        section = models.MarkdownSection(heading="Applying", content=content)

        assert _round_trip(_document(section)).sections[0].content == content


class TestCrossReferences:
    def test_a_stored_link_points_at_an_anchor_a_heading_in_the_file_prints(self):
        """This is what "self-contained" means for a link: the target resolves
        inside the document, with nothing else needed to make sense of it."""
        payment = models.MarkdownSection(heading="Payment", ordinal=2)
        applying = models.MarkdownSection(
            heading="Applying", ordinal=1, content="Continue to [Payment](#_Payment)."
        )
        document = models.MarkdownDocument(
            title="Claims",
            sections=[applying, payment],
            bookmarks={"_Payment": payment},
        )

        rendered = document.markdown(PREFIX)

        assert "[Payment](#2-payment)" in rendered
        assert "## 2 Payment" in rendered

    def test_a_link_follows_the_section_it_points_at_when_that_section_moves(self):
        """The property that makes discarding Word's bookmark names harmless.

        A stored link names the anchor rather than the bookmark, and reading puts
        that name back into the same late-bound state the parser leaves it in - so
        the link is still pointing at a *section*, and re-rendering after the
        section moves writes wherever it moved to.
        """
        payment = models.MarkdownSection(heading="Payment", ordinal=2)
        applying = models.MarkdownSection(
            heading="Applying", ordinal=1, content="Continue to [Payment](#_Payment)."
        )
        document = models.MarkdownDocument(
            title="Claims",
            sections=[applying, payment],
            bookmarks={"_Payment": payment},
        )

        read = _round_trip(document)
        moved, stayed = read.sections[1], read.sections[0]
        moved.ordinal, stayed.ordinal = 1, 2

        assert "[Payment](#1-payment)" in read.markdown(PREFIX)

    def test_a_document_read_and_written_again_is_the_same_document(self):
        """Reading is idempotent where nothing changes: the anchor a link names
        resolves to itself, so a second render moves nothing."""
        payment = models.MarkdownSection(heading="Payment", ordinal=2)
        applying = models.MarkdownSection(
            heading="Applying", ordinal=1, content="Continue to [Payment](#_Payment)."
        )
        document = models.MarkdownDocument(
            title="Claims",
            sections=[applying, payment],
            bookmarks={"_Payment": payment},
        )

        once = document.markdown(PREFIX)

        assert reader.from_markdown(once).markdown(PREFIX) == once
