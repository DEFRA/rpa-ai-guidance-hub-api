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
            ("11-late", "1.1 Late\nPay late."),
        ]

    def test_is_known_by_the_anchor_the_hub_gives_its_heading(self):
        markdown = "## Notes\n\nOne.\n\n## Notes\n\nTwo."

        assert _texts(markdown) == [
            ("notes", "Notes\nOne."),
            ("notes-1", "Notes\nTwo."),
        ]


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
