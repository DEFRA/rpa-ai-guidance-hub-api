"""Links in a guide that name a guide in the hub, pointed at that guide's page.

All fixture text is invented.
"""

from __future__ import annotations

from app.guidance import ids, linked_guides

FORM = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/Claims/The%20Claim%20Form.docx"
    "?d=w1&csf=1&web=1"
)
FORM_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/claims/the%20claim%20form.docx"
)
OTHER = "https://example.sharepoint.com/:w:/r/teams/Team1234/Claims/Other.docx?d=w2"
GUIDE = ids.new_document_id()
PAGE = f"/guides/{GUIDE}"


def _pointed(markdown: str) -> str:
    links = linked_guides.document_links(markdown)
    return linked_guides.pointed_at(markdown, links, {FORM_CANONICAL: GUIDE})


class TestTheLinksToDocumentsInAGuide:
    def test_are_those_of_its_links_that_name_a_document_by_their_canonical_links(
        self,
    ):
        markdown = (
            f"See [the form]({FORM}), [the rules](https://www.gov.uk/rules), "
            f"[section 2](#2-checks) and [the form again]({FORM}).\n"
        )

        links = linked_guides.document_links(markdown)

        assert [link.canonical_link for link in links] == [
            FORM_CANONICAL,
            FORM_CANONICAL,
        ]


class TestALinkToAGuideInTheHub:
    def test_points_at_the_guides_page(self):
        assert _pointed(f"Fill in [the form]({FORM}).\n") == (
            f"Fill in [the form]({PAGE}).\n"
        )

    def test_changes_nothing_else_in_the_guide(self):
        markdown = (
            "# Claims guide\n\n"
            "## 1 Introduction\n\n"
            f"Fill in [the form]({FORM}), then [the other]({OTHER}).\n\n"
            "## 2 Checks\n\n"
            "- [the rules](https://www.gov.uk/rules)\n"
        )

        assert _pointed(markdown) == markdown.replace(f"({FORM})", f"({PAGE})")

    def test_in_a_table_cell_points_at_the_guides_page(self):
        markdown = f"| Form | Use |\n|---|---|\n| [the form]({FORM}) | Claims |\n"

        assert _pointed(markdown) == (
            f"| Form | Use |\n|---|---|\n| [the form]({PAGE}) | Claims |\n"
        )

    def test_in_a_coloured_span_points_at_the_guides_page(self):
        markdown = f"Fill in [[**the form**]{{.red}}]({FORM}).\n"

        assert _pointed(markdown) == f"Fill in [[**the form**]{{.red}}]({PAGE}).\n"

    def test_in_a_list_items_indented_paragraph_points_at_the_guides_page(self):
        markdown = f"1. Check the claim.\n\n    Then fill in [the form]({FORM}).\n"

        assert _pointed(markdown) == (
            f"1. Check the claim.\n\n    Then fill in [the form]({PAGE}).\n"
        )

    def test_in_angle_brackets_as_the_converter_writes_one_with_a_space(self):
        spaced = FORM.replace("The%20Claim%20Form", "The Claim Form")
        markdown = f"Fill in [the form](<{spaced}>).\n"

        assert _pointed(markdown) == f"Fill in [the form]({PAGE}).\n"

    def test_written_twice_points_at_the_guides_page_each_time(self):
        markdown = f"[the form]({FORM})\n\n- [the form again]({FORM})\n"

        assert (
            _pointed(markdown) == f"[the form]({PAGE})\n\n- [the form again]({PAGE})\n"
        )

    def test_after_lines_ended_by_a_carriage_return_alone_keeps_each_ending(self):
        markdown = f"## Claims\r\rForms\r\n\r\nFill in [the form]({FORM}).\n"

        assert _pointed(markdown) == (
            f"## Claims\r\rForms\r\n\r\nFill in [the form]({PAGE}).\n"
        )


class TestWhatIsLeftAsItIs:
    def test_a_link_to_a_document_that_is_no_guide_in_the_hub(self):
        markdown = f"See [the other]({OTHER}).\n"

        assert _pointed(markdown) == markdown

    def test_a_guide_with_no_guides_to_point_at(self):
        markdown = f"Fill in [the form]({FORM}).\n"

        links = linked_guides.document_links(markdown)

        assert linked_guides.pointed_at(markdown, links, {}) == markdown
