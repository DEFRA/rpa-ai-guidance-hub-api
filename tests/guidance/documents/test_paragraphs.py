"""The first paragraph of a section, as plain text: the words that open it, shown under
its heading where the section is bookmarked. All fixture text is invented."""

from app.guidance.documents import paragraphs


class TestFirstParagraph:
    def test_is_the_first_block_of_words_without_their_marks(self):
        content = "\n".join(
            [
                "Check the **claim** and see [the rules](#2-1-rules) first\\_ever.",
                "It goes on.",
                "",
                "More text.",
            ]
        )

        assert paragraphs.first_of(content) == (
            "Check the claim and see the rules first_ever. It goes on."
        )

    def test_is_none_for_a_section_with_no_words_of_its_own(self):
        assert paragraphs.first_of("") is None

    def test_passes_over_a_picture_alone_and_reads_a_list_item(self):
        content = "\n".join(
            [
                "![A map](../assets/abc.png)",
                "",
                "- Fill in [the form]{.red} &amp; <u>send</u> it",
            ]
        )

        assert paragraphs.first_of(content) == "Fill in the form & send it"

    def test_passes_over_code_and_tables(self):
        content = "\n".join(
            [
                "```",
                "## not a heading",
                "```",
                "",
                "| a | b |",
                "| - | - |",
                "",
                "Last words.",
            ]
        )

        assert paragraphs.first_of(content) == "Last words."

    def test_reads_a_line_break_as_a_space_and_an_escape_as_its_character(self):
        assert paragraphs.first_of("One<br>two \\* three") == "One two * three"
