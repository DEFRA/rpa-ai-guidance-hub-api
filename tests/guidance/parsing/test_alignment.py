"""Lining a rendered document's tables up into the editor's canonical shape.

Two things are pinned. The first is the shape itself, which is not a matter of taste:
the editor rewrites every table it saves into it, so anything else comes back changed
by the first save a person makes.

The second is *when* it happens. A cell's width is not knowable while the table is
being built, because a cross-reference and an image path are both still placeholders
at that point and both change length when they are filled. The cases below that render
a whole document rather than reading a section's content are the ones holding that
down.

All fixture text is invented, as everywhere in this package.
"""

from app.guidance.parsing import alignment, models


class TestTheShape:
    def test_a_column_is_as_wide_as_its_widest_cell(self):
        assert alignment.aligned(
            "| A | Considerably wider |\n| --- | --- |\n| Withdrawal | B |"
        ) == (
            "| A          | Considerably wider |\n"
            "| ---------- | ------------------ |\n"
            "| Withdrawal | B                  |"
        )

    def test_a_narrow_column_is_still_wide_enough_to_delimit(self):
        """Three dashes is the shortest run GFM accepts, so a column narrower than
        that is written out to the floor rather than to its content."""
        assert alignment.aligned("| A |\n| --- |\n| B |") == (
            "| A   |\n| --- |\n| B   |"
        )

    def test_an_escaped_pipe_is_content_and_not_a_column(self):
        """The backslash is what says which pipes end a cell. Splitting on every
        pipe would find columns a row does not have and pad against them."""
        assert alignment.aligned("| a \\| b | c |\n| --- | --- |\n| d | e |") == (
            "| a \\| b | c   |\n| ------ | --- |\n| d      | e   |"
        )

    def test_a_row_short_of_columns_is_filled_out(self):
        """A row Word left ragged still has to line up with the rest."""
        assert alignment.aligned("| a | b |\n| --- | --- |\n| c |") == (
            "| a   | b   |\n| --- | --- |\n| c   |     |"
        )

    def test_aligning_twice_changes_nothing(self):
        """The whole point: what it writes is already what it would write again."""
        once = alignment.aligned("| A | Wider |\n| --- | --- |\n| Withdrawal | B |")
        assert alignment.aligned(once) == once


class TestWhatIsNotATable:
    def test_prose_is_left_alone(self):
        markdown = "Complete the form.\n\nThen send it."
        assert alignment.aligned(markdown) == markdown

    def test_a_line_of_pipes_without_a_delimiter_is_not_a_table(self):
        """A paragraph can begin with a pipe. Only the delimiter under the first row
        makes what follows a table, and requiring it is what keeps this off prose."""
        markdown = "| not a table\n| nor this |"
        assert alignment.aligned(markdown) == markdown

    def test_a_table_ends_where_its_rows_stop(self):
        assert alignment.aligned("| a |\n| --- |\n| b |\n\nAfter.") == (
            "| a   |\n| --- |\n| b   |\n\nAfter."
        )

    def test_two_tables_are_measured_apart(self):
        """Each table's columns are its own; one wide cell must not pad the other."""
        assert alignment.aligned(
            "| a |\n| --- |\n| b |\n\n| Considerably wider |\n| --- |\n| c |"
        ) == (
            "| a   |\n| --- |\n| b   |\n\n"
            "| Considerably wider |\n| ------------------ |\n| c                  |"
        )


class TestWhenItHappens:
    """A cell's width is only knowable once every hole in it is filled."""

    def test_a_cross_reference_is_measured_after_it_resolves(self):
        """The bookmark Word names is longer than the number it resolves to, so a
        table measured before the rewrite is padded to a width no cell has. This is
        the case that sent a real conversion out 13 columns wide."""
        target = models.MarkdownSection(heading="Transition failed", ordinal=2)
        section = models.MarkdownSection(
            heading="Applying",
            content=(
                "| Case | Action |\n"
                "| --- | --- |\n"
                "| Withdrawal | See [step](#_Toc178312345) |"
            ),
        )

        rendered = section.markdown(bookmarks={"_Toc178312345": target})

        assert "| Withdrawal | See [step](#2) |" in rendered
        assert "| ---------- | -------------- |" in rendered

    def test_an_image_path_is_measured_after_its_prefix(self):
        """The prefix makes a cell longer, where a cross-reference makes it shorter.
        Both are only known at render time, and both have to be measured then."""
        section = models.MarkdownSection(
            heading="Applying",
            content="| Case | Figure |\n| --- | --- |\n| Withdrawal | ![](1_img_1.png) |",
            images=[
                models.Image(name="1_img_1.png", data=b"", content_type="image/png")
            ],
        )

        rendered = section.markdown(image_prefix="https://example.org/docs/")

        assert "![](https://example.org/docs/1_img_1.png)" in rendered
        widths = {len(line) for line in rendered.split("\n") if line.startswith("|")}
        assert len(widths) == 1, "every row of a table is the same width"
