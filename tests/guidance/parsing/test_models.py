"""The Markdown document model: derived numbering, template filling, assembly."""

from app.guidance.parsing import models


def _image(name: str = "1_img_1.png") -> models.Image:
    return models.Image(name=name, data=b"\x89PNG", content_type="image/png")


class TestSectionNumbering:
    def test_a_top_level_section_is_numbered_by_its_ordinal(self):
        assert models.MarkdownSection(heading="Payments", ordinal=3).number == "3"

    def test_a_nested_section_extends_its_parent_number(self):
        parent = models.MarkdownSection(heading="Eligibility", ordinal=3)
        child = models.MarkdownSection(
            heading="Evidence required", ordinal=1, parent=parent
        )
        grandchild = models.MarkdownSection(heading="Appeals", ordinal=2, parent=child)

        assert child.number == "3.1"
        assert grandchild.number == "3.1.2"

    def test_a_number_follows_its_parent_rather_than_being_stored(self):
        """The point of deriving it: no stored number can disagree with the structure.

        Renumbering a parent renumbers everything beneath it, with nothing to keep
        in step by hand.
        """
        parent = models.MarkdownSection(heading="Eligibility", ordinal=3)
        child = models.MarkdownSection(
            heading="Evidence required", ordinal=1, parent=parent
        )

        parent.ordinal = 5

        assert child.number == "5.1"

    def test_depth_counts_top_level_sections_as_one(self):
        parent = models.MarkdownSection(heading="Eligibility")
        child = models.MarkdownSection(heading="Evidence required", parent=parent)

        assert parent.depth == 1
        assert child.depth == 2


class TestImagePrefixes:
    def test_an_image_path_is_prefixed_at_render_time(self):
        section = models.MarkdownSection(
            heading="Evidence",
            content="Before.\n\n![A screenshot](1_img_1.png)\n\nAfter.",
            images=[_image()],
        )

        assert "![A screenshot](docs/42/images/1_img_1.png)" in section.markdown(
            "docs/42/images/"
        )

    def test_the_bare_name_is_used_when_no_prefix_is_given(self):
        section = models.MarkdownSection(
            heading="Evidence",
            content="![A screenshot](1_img_1.png)",
            images=[_image()],
        )

        assert "![A screenshot](1_img_1.png)" in section.markdown()

    def test_the_same_section_renders_for_more_than_one_destination(self):
        """The content is a template, so one parse serves S3 and a local directory."""
        section = models.MarkdownSection(
            heading="Evidence", content="![x](1_img_1.png)", images=[_image()]
        )

        assert "(s3/1_img_1.png)" in section.markdown("s3/")
        assert "(local/1_img_1.png)" in section.markdown("local/")

    def test_prose_matching_an_image_name_is_left_alone(self):
        """Only a link target is rewritten, never the document's own words."""
        section = models.MarkdownSection(
            heading="Evidence",
            content="The file 1_img_1.png is attached.\n\n![x](1_img_1.png)",
            images=[_image()],
        )

        rendered = section.markdown("images/")

        assert "The file 1_img_1.png is attached." in rendered
        assert "![x](images/1_img_1.png)" in rendered


class TestDocumentMarkdown:
    def test_a_document_with_no_sections_renders_only_its_title(self):
        assert models.MarkdownDocument(title="Example Guide").markdown() == (
            "# Example Guide\n"
        )

    def test_sections_render_one_heading_level_below_their_depth(self):
        parent = models.MarkdownSection(heading="Eligibility", ordinal=3)
        child = models.MarkdownSection(
            heading="Evidence required", ordinal=1, parent=parent
        )
        document = models.MarkdownDocument(
            title="Example Guide", sections=[parent, child]
        )

        rendered = document.markdown()

        assert "## 3 Eligibility" in rendered
        assert "### 3.1 Evidence required" in rendered

    def test_the_document_is_the_ordered_concatenation_of_its_sections(self):
        """One rendering path, not two: the document is a fold over the sections."""
        first = models.MarkdownSection(heading="Introduction", ordinal=1)
        second = models.MarkdownSection(heading="Payments", ordinal=2)
        document = models.MarkdownDocument(
            title="Example Guide", sections=[first, second]
        )

        rendered = document.markdown()

        assert rendered.index("## 1 Introduction") < rendered.index("## 2 Payments")
        assert first.markdown() in rendered
        assert second.markdown() in rendered

    def test_a_sections_content_follows_its_heading(self):
        section = models.MarkdownSection(
            heading="Introduction", content="This guide covers the process."
        )
        document = models.MarkdownDocument(title="Guide", sections=[section])

        assert document.markdown() == (
            "# Guide\n\n## 1 Introduction\n\nThis guide covers the process.\n"
        )

    def test_images_are_gathered_from_every_section_in_order(self):
        first = models.MarkdownSection(heading="One", images=[_image("1_img_1.png")])
        second = models.MarkdownSection(heading="Two", images=[_image("2_img_1.png")])
        document = models.MarkdownDocument(sections=[first, second])

        assert [image.name for image in document.images] == [
            "1_img_1.png",
            "2_img_1.png",
        ]
