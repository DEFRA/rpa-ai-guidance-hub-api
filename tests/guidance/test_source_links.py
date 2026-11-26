"""The SharePoint link a maintainer gives for the document they upload.

All links are invented, as everywhere in this package.
"""

import pytest

from app.guidance import source_links

WORD = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/Claims/The%20Claim%20Form.docx"
    "?d=w1&csf=1&web=1"
)
WORD_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/claims/the%20claim%20form.docx"
)
DOCUMENT_ID = (
    "https://example.sharepoint.com/teams/Team1234/_layouts/15/DocIdRedir.aspx"
    "?ID=RPAINTRA-405-3434"
)


class TestALinkAccepted:
    def test_to_a_named_word_document_is_kept_as_given_with_its_canonical_link(self):
        assert source_links.checked(WORD) == source_links.SourceLink(
            WORD, WORD_CANONICAL
        )

    def test_by_document_id_is_kept_with_its_id_alone(self):
        checked = source_links.checked(f"{DOCUMENT_ID}&web=1")

        assert checked.canonical_link == (
            "https://example.sharepoint.com/teams/team1234/_layouts/15/docidredir.aspx"
            "?id=rpaintra-405-3434"
        )

    def test_behind_a_safe_link_has_the_canonical_link_of_the_link_it_wraps(self):
        wrapped = (
            "https://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
            "example.sharepoint.com%2Fteams%2FTeam1234%2FClaims%2F"
            "The%2520Claim%2520Form.docx&data=05%7C02"
        )

        assert source_links.checked(wrapped).canonical_link == WORD_CANONICAL


class TestALinkRefused:
    @pytest.mark.parametrize(
        ("link", "reason"),
        [
            pytest.param(
                "https://example.sharepoint.com/:f:/r/teams/Team1234/Claims",
                source_links.Refusal.NOT_A_WORD_DOCUMENT,
                id="a folder",
            ),
            pytest.param(
                "https://example.sharepoint.com/teams/Team1234/Claims/Rates.xlsx",
                source_links.Refusal.NOT_A_WORD_DOCUMENT,
                id="another kind of file",
            ),
            pytest.param(
                "https://www.gov.uk/guidance/claims",
                source_links.Refusal.NOT_SHAREPOINT,
                id="another site",
            ),
            pytest.param(
                "file://share.example.gov.uk/Shared/Claims.docx",
                source_links.Refusal.NOT_SHAREPOINT,
                id="a network share",
            ),
        ],
    )
    def test_says_why(self, link, reason):
        with pytest.raises(source_links.RefusedSourceLinkError) as refused:
            source_links.checked(link)

        assert refused.value.reason == reason
