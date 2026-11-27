"""The words a reader sees in each section of a guide.

All fixture text is invented.
"""

from __future__ import annotations

from app.guidance import reading


def _texts(markdown: str) -> list[tuple[str | None, str]]:
    return [(section.anchor, section.text) for section in reading.sections_of(markdown)]


class TestASection:
    def test_runs_from_its_heading_to_the_next_heading_of_any_level(self):
        markdown = "# Paying claims\n\n## 1 Paying\n\nPay on time.\n\n### 1.1 Late\n\nPay late."

        assert _texts(markdown) == [
            ("1-paying", "1 Paying\nPay on time."),
            ("1-1-late", "1.1 Late\nPay late."),
        ]

    def test_is_known_by_the_anchor_the_hub_gives_its_heading(self):
        markdown = "## Notes\n\nOne.\n\n## Notes\n\nTwo."

        assert _texts(markdown) == [
            ("notes", "Notes\nOne."),
            ("notes-1", "Notes\nTwo."),
        ]


class TestASectionHeading:
    def test_is_never_a_line_in_a_code_block(self):
        markdown = (
            "# Claims\n\n```\n## Not a heading\n```\n\n"
            "## Steps\n\nRun:\n\n```\n## Example\n```\n\nDone."
        )

        assert _texts(markdown) == [
            (None, "## Not a heading"),
            ("steps", "Steps\nRun:\n## Example\nDone."),
        ]

    def test_is_never_one_in_a_quote_whose_words_read_on_in_the_section(self):
        markdown = "## Claims\n\nPay.\n\n> ## Note\n>\n> Pay late."

        assert _texts(markdown) == [("claims", "Claims\nPay.\nNote\nPay late.")]

    def test_is_known_by_its_words_as_written_with_their_spaces_as_pins_know_it(self):
        assert _texts("## 1 Claim  payments\n\nPay.") == [
            ("1-claim--payments", "1 Claim payments\nPay.")
        ]

    def test_is_known_as_the_page_knows_it_by_its_words_and_entities_as_written(self):
        markdown = (
            "## [Payment](#detail)\n\nPay.\n\n## 1 Terms &amp; conditions\n\nAgree."
        )

        assert _texts(markdown) == [
            ("payment", "Payment\nPay."),
            ("1-terms-amp-conditions", "1 Terms & conditions\nAgree."),
        ]

    def test_is_known_as_the_page_knows_it_by_its_tags_colours_and_pictures_words(self):
        markdown = (
            "## [Pay]{.red}\n\nPay.\n\n## <u>Pay</u>\n\nLate.\n\n"
            "## Charts ![A chart](assets/chart.png)\n\nSee."
        )

        assert _texts(markdown) == [
            ("payred", "Pay\nPay."),
            ("upayu", "Pay\nLate."),
            ("charts-a-chart", "Charts\nSee."),
        ]

    def test_is_never_an_underlined_one_whose_words_read_on_in_the_section(self):
        assert _texts("## Claims\n\nPay.\n\nLate\n----\n\nPay late.") == [
            ("claims", "Claims\nPay.\nLate\nPay late.")
        ]

    def test_keeps_every_word_even_where_it_reads_like_a_numbered_list(self):
        assert _texts("## 2024. Rates\n\nPay.") == [("2024-rates", "2024. Rates\nPay.")]


class TestTheIntroduction:
    def test_is_the_text_before_the_first_section_heading_with_no_anchor(self):
        markdown = "# Paying claims\n\nRead this first.\n\n## 1 Paying\n\nPay."

        assert _texts(markdown) == [
            (None, "Read this first."),
            ("1-paying", "1 Paying\nPay."),
        ]


class TestTheText:
    def test_has_one_block_to_a_line(self):
        markdown = (
            "## Steps\n\nFirst read this.\n\n- Fill in the form\n- Send it\n\n"
            "| Field | Value |\n| --- | --- |\n| Name | Ann Example |\n\n"
            "```\nsend --now\n```"
        )

        lines = [
            "Steps",
            "First read this.",
            "Fill in the form",
            "Send it",
            "Field",
            "Value",
            "Name",
            "Ann Example",
            "send --now",
        ]

        assert _texts(markdown) == [("steps", "\n".join(lines))]

    def test_has_one_space_between_words_where_a_paragraph_wraps(self):
        assert _texts("## Paying\n\nPay the\nclaim   in full.") == [
            ("paying", "Paying\nPay the claim in full.")
        ]

    def test_has_no_marks_only_the_words_they_mark(self):
        markdown = "## Paying\n\nPay **in full** by _noon_, `today`.\n\nFill in [your name]{.red}."

        assert _texts(markdown) == [
            ("paying", "Paying\nPay in full by noon, today.\nFill in your name.")
        ]

    def test_keeps_the_words_of_a_link_but_not_where_it_goes(self):
        markdown = "## Forms\n\nUse the [claim form](https://example.com/pay-claim)."

        assert _texts(markdown) == [("forms", "Forms\nUse the claim form.")]

    def test_keeps_the_words_of_a_link_to_a_network_share_but_not_where_it_goes(self):
        markdown = "## Forms\n\nOpen [the claim folder](file://example-share/claims)."

        assert _texts(markdown) == [("forms", "Forms\nOpen the claim folder.")]

    def test_has_no_words_for_a_picture(self):
        markdown = "## Charts\n\n![A chart of payments](assets/chart.png)\n\nSee above."

        assert _texts(markdown) == [("charts", "Charts\nSee above.")]


def _only_link(markdown: str) -> reading.Link:
    (link,) = reading.links_of(markdown)
    return link


class TestALink:
    def test_is_its_text_and_where_it_goes_as_written(self):
        link = _only_link("See [the eligibility rules](https://example.com/rules).")

        assert link.text == "the eligibility rules"
        assert link.destination == "https://example.com/rules"

    def test_keeps_brackets_in_where_it_goes(self):
        """A pattern stops at the first `)`."""
        link = _only_link(
            "[Matrix](https://example.sharepoint.com/Doc.aspx?file=Matrix%20(2021%20Onwards).xlsx)"
        )

        assert link.destination == (
            "https://example.sharepoint.com/Doc.aspx?file=Matrix%20(2021%20Onwards).xlsx"
        )

    def test_in_angle_brackets_keeps_its_spaces(self):
        link = _only_link("[The form](<https://example.com/some path/The form.docx>)")

        assert link.destination == "https://example.com/some path/The form.docx"

    def test_has_plain_text_without_its_marks_or_escapes(self):
        link = _only_link(
            "[**Read** *the* \\[draft\\] ~~now~~ <u>here</u>](https://example.com)"
        )

        assert link.text == "Read the [draft] now here"

    def test_to_a_network_share_is_read_as_a_link(self):
        link = _only_link(
            "[Draft Letter](file://share.example.gov.uk/user/Shared/Draft%20Letter)"
        )

        assert (
            link.destination == "file://share.example.gov.uk/user/Shared/Draft%20Letter"
        )

    def test_to_a_script_is_not_a_link(self):
        assert reading.links_of("[Click](javascript:alert(1))") == []

    def test_within_the_guide_is_a_link_to_its_anchor(self):
        link = _only_link("[see section 3.1](#31-overview)")

        assert link.destination == "#31-overview"

    def test_in_a_table_cell_is_read(self):
        link = _only_link(
            "| Form | Use |\n|---|---|\n| [The form](https://example.com/f.docx)<br>new | Claims |"
        )

        assert link.text == "The form"

    def test_a_picture_is_not_a_link(self):
        assert reading.links_of("![](a3f9.png)") == []


class TestAColourSpanAndALink:
    def test_a_colour_span_is_not_a_link(self):
        assert reading.links_of("A [linked case]{.red} is open.") == []

    def test_a_colour_span_in_a_link_s_text_leaves_the_link_whole(self):
        link = _only_link("[[**Draft letter**]{.red}](https://example.com/letter.docx)")

        assert link.text == "Draft letter"
        assert link.destination == "https://example.com/letter.docx"

    def test_a_colour_span_holding_a_link_leaves_the_link_whole(self):
        link = _only_link("[Use [the form](https://example.com/f.docx) first]{.blue}")

        assert link.text == "the form"
        assert link.destination == "https://example.com/f.docx"


class TestWhereALinkIs:
    def test_is_the_section_it_is_in_and_its_order_there(self):
        markdown = (
            "[before](https://example.com/0)\n\n"
            "# Claims guide\n\n"
            "## 1 Introduction\n\n"
            "[one](https://example.com/1) and [two](https://example.com/2)\n\n"
            "### 1.1 Before you begin\n\n"
            "- [three](https://example.com/3)\n"
        )

        assert [
            (link.section, link.order, link.text) for link in reading.links_of(markdown)
        ] == [
            (None, 1, "before"),
            ("1-introduction", 1, "one"),
            ("1-introduction", 2, "two"),
            ("1-1-before-you-begin", 1, "three"),
        ]

    def test_under_a_heading_repeated_is_in_its_numbered_section(self):
        markdown = (
            "## Notes\n\n[one](https://example.com/1)\n\n"
            "## Notes\n\n[two](https://example.com/2)\n"
        )

        assert [link.section for link in reading.links_of(markdown)] == [
            "notes",
            "notes-1",
        ]

    def test_is_not_found_in_a_code_block(self):
        assert reading.links_of("```\n[not](https://example.com)\n```") == []


ADDRESS = "https://example.com/form.docx"


def _addresses(markdown: str) -> list[tuple[str, str, int]]:
    """Each link's destination, its address as written, and where that starts."""
    return [
        (address.destination, markdown[address.start : address.end], address.start)
        for address in reading.link_addresses(markdown)
    ]


class TestWhereALinksAddressIs:
    def test_is_where_it_is_written_in_a_paragraph(self):
        markdown = f"# Claims\n\nFill in [the form]({ADDRESS}) today.\n"

        assert _addresses(markdown) == [(ADDRESS, ADDRESS, markdown.index(ADDRESS))]

    def test_in_a_list_quote_or_heading_is_where_it_is_written(self):
        markdown = (
            f"## 1 See [the form]({ADDRESS})\n\n"
            f"- Fill in [the form]({ADDRESS}/1)\n\n"
            f"> Then [the form]({ADDRESS}/2)\n\n"
            f"1. Check.\n\n    Then [the form]({ADDRESS}/3).\n"
        )

        assert [start for *_, start in _addresses(markdown)] == [
            markdown.index(f"{ADDRESS})"),
            markdown.index(f"{ADDRESS}/1"),
            markdown.index(f"{ADDRESS}/2"),
            markdown.index(f"{ADDRESS}/3"),
        ]

    def test_is_not_the_same_text_in_inline_code_or_escaped_brackets_before_it(self):
        markdown = (
            f"| `[x]({ADDRESS})` | \\[x\\]({ADDRESS}) | [the form]({ADDRESS}) |\n"
            "|---|---|---|\n"
        )

        assert _addresses(markdown) == [
            (ADDRESS, ADDRESS, markdown.rindex(ADDRESS)),
        ]

    def test_in_angle_brackets_takes_them_in(self):
        markdown = f'Fill in [the form](<{ADDRESS}> "The form").\n'

        assert _addresses(markdown) == [
            (ADDRESS, f"<{ADDRESS}>", markdown.index(f"<{ADDRESS}>"))
        ]

    def test_with_an_escape_is_as_written_and_goes_where_it_says(self):
        markdown = "Fill in [the form](https://example.com/the\\_form.docx).\n"

        assert _addresses(markdown) == [
            (
                "https://example.com/the_form.docx",
                "https://example.com/the\\_form.docx",
                markdown.index("https"),
            )
        ]

    def test_after_lines_ended_by_a_carriage_return_is_where_it_is_written(self):
        markdown = f"## Claims\r\rForms\r\n\r\nFill in [the form]({ADDRESS}).\n"

        assert _addresses(markdown) == [(ADDRESS, ADDRESS, markdown.index(ADDRESS))]

    def test_of_a_reference_style_link_is_not_given(self):
        assert reading.link_addresses(f"[the form][f]\n\n[f]: {ADDRESS}\n") == []
