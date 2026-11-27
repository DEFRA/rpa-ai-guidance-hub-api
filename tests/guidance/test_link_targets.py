"""What a link in a guide points at: its kind, and its canonical link.

Each case is a form of link seen in the guides (554 links, read on 2026-10-30), with
the site, ids and names invented, as everywhere in this package.
"""

import urllib.parse

import pytest

from app.guidance import link_targets

DOC_ID = "%7BD9BCA9F7-37CF-49DE-8ED1-40FC870651BD%7D"

WORD_BY_ID = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/_layouts/15/Doc.aspx"
    f"?sourcedoc={DOC_ID}&file=2016%20Basic%20Navigation%20Guide.docx"
    "&action=default&mobileredirect=true&DefaultItemOpen=1"
)
WORD_BY_ID_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/_layouts/15/doc.aspx"
    "?sourcedoc={d9bca9f7-37cf-49de-8ed1-40fc870651bd}"
    "&file=2016%20basic%20navigation%20guide.docx"
)

WORD_BY_PATH = (
    "https://example.sharepoint.com/:w:/r/teams/Team1234/Content%20Cloud/Call,%20Letter"
    "%20Handling/Making%20Outbound%20Calls.docx?d=w6505a4ff9aeb42afb0a429aed0337367"
    "&csf=1&web=1&e=0Rau3u"
)
WORD_BY_PATH_CANONICAL = (
    "https://example.sharepoint.com/teams/team1234/content%20cloud/call%2C%20letter"
    "%20handling/making%20outbound%20calls.docx"
)


def _target(link: str) -> link_targets.Target:
    return link_targets.target_of(link)


class TestANamedWordDocument:
    def test_by_its_id_keeps_only_the_id_and_the_name_evened(self):
        target = _target(WORD_BY_ID)

        assert target.kind == link_targets.Kind.WORD_DOCUMENT
        assert target.canonical_link == WORD_BY_ID_CANONICAL
        assert target.file_name == "2016 Basic Navigation Guide.docx"

    def test_by_its_id_written_another_way_has_the_same_canonical_link(self):
        """Spaces as `+`, the id's braces in lower case, and other query noise."""
        link = (
            "https://example.sharepoint.com/:w:/r/teams/Team1234/_layouts/15/Doc.aspx"
            "?sourcedoc=%7bD9BCA9F7-37CF-49DE-8ED1-40FC870651BD%7d"
            "&file=2016+Basic+Navigation+Guide.docx&action=default&xsdata=MDV8MDJ8"
            "&sdata=abc%3D"
        )

        assert _target(link).canonical_link == WORD_BY_ID_CANONICAL

    def test_with_its_query_in_another_order_has_the_same_canonical_link(self):
        """What identifies it is kept in one order, whatever order the link gives, so
        two links match by plain equality."""
        link = (
            "https://example.sharepoint.com/:w:/r/teams/Team1234/_layouts/15/Doc.aspx"
            "?action=default&file=2016%20Basic%20Navigation%20Guide.docx"
            f"&sourcedoc={DOC_ID}"
        )

        assert _target(link).canonical_link == WORD_BY_ID_CANONICAL

    def test_by_its_path_keeps_only_the_path(self):
        target = _target(WORD_BY_PATH)

        assert target.kind == link_targets.Kind.WORD_DOCUMENT
        assert target.canonical_link == WORD_BY_PATH_CANONICAL
        assert target.file_name == "Making Outbound Calls.docx"

    def test_by_its_path_without_the_word_mark_has_the_same_canonical_link(self):
        link = (
            "https://example.sharepoint.com/teams/Team1234/Content%20Cloud/"
            "Call,%20Letter%20Handling/Making%20Outbound%20Calls.docx?Web=1"
            "&Source=http%3A%2F%2Fold%2Dhub%2Fpages%2Fdefault%2Easpx"
        )

        assert _target(link).canonical_link == WORD_BY_PATH_CANONICAL

    def test_by_its_path_with_brackets_in_its_name(self):
        link = (
            "https://example.sharepoint.com/:w:/r/teams/Team1234/SAAC/2026%20Processing"
            "%20Crosscheck%20(SAAC).docx?d=w1&csf=1&web=1"
        )

        target = _target(link)

        assert target.kind == link_targets.Kind.WORD_DOCUMENT
        assert target.canonical_link == (
            "https://example.sharepoint.com/teams/team1234/saac/2026%20processing"
            "%20crosscheck%20%28saac%29.docx"
        )
        assert target.file_name == "2026 Processing Crosscheck (SAAC).docx"

    def test_in_an_old_viewer_frame_is_named_by_the_path_it_holds(self):
        """`WopiFrame2.aspx` holds the document's path, unencoded, its own query
        (`?Web=1`) run into the frame's."""
        link = (
            "https://example.sharepoint.com/teams/Team1234/_layouts/WopiFrame2.aspx"
            "?sourcedoc=/operations/bps/Documents/Basic%20Navigation%20Guide.docx"
            "?Web=1&Source=http%3A%2F%2Fold%2Dhub%2Fpages"
        )

        target = _target(link)

        assert target.kind == link_targets.Kind.WORD_DOCUMENT
        assert target.canonical_link == (
            "https://example.sharepoint.com/teams/team1234/_layouts/wopiframe2.aspx"
            "?sourcedoc=/operations/bps/documents/basic%20navigation%20guide.docx"
        )
        assert target.file_name == "Basic Navigation Guide.docx"

    def test_in_a_library_view_is_named_by_the_path_its_id_holds(self):
        link = (
            "https://example.sharepoint.com/sites/Claims/Shared%20Documents/Forms/"
            "AllItems.aspx?id=%2Fsites%2FClaims%2FShared%20Documents%2FClaim%20Form.docx"
            "&parent=%2Fsites%2FClaims%2FShared%20Documents"
        )

        target = _target(link)

        assert target.kind == link_targets.Kind.WORD_DOCUMENT
        assert target.canonical_link == (
            "https://example.sharepoint.com/sites/claims/shared%20documents/forms/"
            "allitems.aspx?id=/sites/claims/shared%20documents/claim%20form.docx"
        )
        assert target.file_name == "Claim Form.docx"

    def test_behind_a_safe_link_is_the_link_it_wraps(self):
        link = (
            "https://eur03.safelinks.protection.outlook.com/?url="
            "https%3A%2F%2Fexample.sharepoint.com%2Fteams%2FTeam1234%2FContent%2520Cloud"
            "%2FCall%2C%2520Letter%2520Handling%2FMaking%2520Outbound%2520Calls.docx"
            "&data=05%7C02%7C&reserved=0"
        )

        assert _target(link).canonical_link == WORD_BY_PATH_CANONICAL


DOCUMENT_ID_LINK = (
    "https://example.sharepoint.com/teams/Team1234/_layouts/15/DocIdRedir.aspx"
    "?ID=RPAINTRA-405-3434"
)


class TestADocumentIdLink:
    """The link SharePoint's Document ID service gives a document, which stays the
    same when the document moves."""

    def test_keeps_only_the_id(self):
        target = _target(f"{DOCUMENT_ID_LINK}&web=1")

        assert target.kind == link_targets.Kind.DOCUMENT_ID
        assert target.canonical_link == (
            "https://example.sharepoint.com/teams/team1234/_layouts/15/docidredir.aspx"
            "?id=rpaintra-405-3434"
        )
        assert target.file_name is None

    def test_keeps_its_id_alone_whatever_else_its_query_names(self):
        """So one document has one canonical link, however it is linked by its ID."""
        link = f"{DOCUMENT_ID_LINK}&file=Old%20Name.docx&sourcedoc=%7BAB12%7D"

        assert _target(link).canonical_link == _target(DOCUMENT_ID_LINK).canonical_link

    def test_behind_a_safe_link_is_the_link_it_wraps(self):
        link = (
            "https://eur03.safelinks.protection.outlook.com/?url="
            + urllib.parse.quote(DOCUMENT_ID_LINK, safe="")
            + "&data=05%7C02"
        )

        assert _target(link) == _target(DOCUMENT_ID_LINK)

    def test_naming_a_word_file_keeps_its_name(self):
        target = _target(f"{DOCUMENT_ID_LINK}&file=Claim%20Form.docx")

        assert (target.kind, target.file_name) == (
            link_targets.Kind.DOCUMENT_ID,
            "Claim Form.docx",
        )

    def test_of_the_same_name_outside_the_layouts_folder_is_another_sharepoint_item(
        self,
    ):
        """Only SharePoint's own handler, under `_layouts`, redirects by the ID."""
        link = "https://example.sharepoint.com/teams/T/SitePages/DocIdRedir.aspx?ID=42"

        assert _target(link).kind == link_targets.Kind.SHAREPOINT_OTHER

    def test_without_an_id_is_another_sharepoint_item(self):
        link = (
            "https://example.sharepoint.com/teams/Team1234/_layouts/15/DocIdRedir.aspx"
        )

        assert _target(link).kind == link_targets.Kind.SHAREPOINT_OTHER


class TestAnotherSharePointItem:
    @pytest.mark.parametrize(
        ("link", "canonical"),
        [
            pytest.param(
                "https://example.sharepoint.com/teams/Team1234/Content%20Cloud1/Forms/"
                "countrysidestewardship.aspx?id=%2Fteams%2FTeam1234%2FContent%20Cloud1"
                "%2FGeneral&viewid=afbda9de%2Dc89a%2D4739",
                "https://example.sharepoint.com/teams/team1234/content%20cloud1/forms/"
                "countrysidestewardship.aspx?id=/teams/team1234/content%20cloud1/general",
                id="a folder, by a library view's id",
            ),
            pytest.param(
                "https://example.sharepoint.com/:w:/t/Team1234/ET_QrNHrbulAixfnCoisH4EBSc",
                "https://example.sharepoint.com/:w:/t/team1234/et_qrnhrbulaixfncoish4ebsc",
                id="an opaque sharing link, Word but not naming the file",
            ),
            pytest.param(
                "https://example.sharepoint.com/:x:/r/teams/Team1234/_layouts/15/Doc.aspx"
                f"?sourcedoc={DOC_ID}&file=Compatibility%20Matrix%20(2021%20Onwards).xlsx"
                "&action=default",
                "https://example.sharepoint.com/teams/team1234/_layouts/15/doc.aspx"
                "?sourcedoc={d9bca9f7-37cf-49de-8ed1-40fc870651bd}"
                "&file=compatibility%20matrix%20%282021%20onwards%29.xlsx",
                id="a spreadsheet, by its id",
            ),
            pytest.param(
                "https://example.sharepoint.com/:x:/r/teams/Team1234/Claims/Option%20"
                "Intersections.xlsx?d=wb1a7&csf=1&web=1&e=qmy484",
                "https://example.sharepoint.com/teams/team1234/claims/option%20"
                "intersections.xlsx",
                id="a spreadsheet, by its path",
            ),
            pytest.param(
                "https://example.sharepoint.com/teams/Team1234/Content%20Cloud/Forms/"
                "FraudRiskManagement.aspx?xsdata=MDV8MDJ8&sdata=abc",
                "https://example.sharepoint.com/teams/team1234/content%20cloud/forms/"
                "fraudriskmanagement.aspx",
                id="a page",
            ),
            pytest.param(
                f"https://example.sharepoint.com/:w:/r/teams/Team1234/_layouts/15/Doc.aspx?sourcedoc={DOC_ID}",
                "https://example.sharepoint.com/teams/team1234/_layouts/15/doc.aspx"
                "?sourcedoc={d9bca9f7-37cf-49de-8ed1-40fc870651bd}",
                id="a Word document by its id alone, not naming the file",
            ),
        ],
    )
    def test_keeps_only_what_identifies_it(self, link, canonical):
        target = _target(link)

        assert target.kind == link_targets.Kind.SHAREPOINT_OTHER
        assert target.canonical_link == canonical


class TestALinkNeverCanonicalised:
    @pytest.mark.parametrize(
        ("link", "kind"),
        [
            pytest.param(
                "https://www.gov.uk/government/publications/record-keeping",
                link_targets.Kind.EXTERNAL,
                id="another site",
            ),
            pytest.param(
                "https://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
                "www.gov.uk%2Fguidance%2Fusing-fertilisers&data=05%7C02",
                link_targets.Kind.EXTERNAL,
                id="another site behind a safe link",
            ),
            pytest.param(
                "javascript://example.sharepoint.com/Guide.docx",
                link_targets.Kind.EXTERNAL,
                id="a SharePoint host reached by neither http nor https",
            ),
            pytest.param(
                "javascript://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
                "example.sharepoint.com%2FGuide.docx",
                link_targets.Kind.EXTERNAL,
                id="a safe link reached by neither http nor https",
            ),
            pytest.param(
                "https://attacker.invalid\\@example.sharepoint.com/Guide.docx",
                link_targets.Kind.EXTERNAL,
                id="a backslash before the host, which a browser reads as a slash",
            ),
            pytest.param(
                "https://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
                "attacker.invalid%5C%40example.sharepoint.com%2FGuide.docx",
                link_targets.Kind.EXTERNAL,
                id="the same behind a safe link",
            ),
            pytest.param(
                "https://[broken",
                link_targets.Kind.EXTERNAL,
                id="an address that cannot be read",
            ),
            pytest.param(
                "https://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
                "%5Bbroken&data=05%7C02",
                link_targets.Kind.EXTERNAL,
                id="an address that cannot be read behind a safe link",
            ),
            pytest.param(
                "https://example.sharepoint.com:not-a-port/_layouts/15/"
                "DocIdRedir.aspx?ID=A-1-2",
                link_targets.Kind.EXTERNAL,
                id="a port that is not a number",
            ),
            pytest.param(
                "https://example.sharepoint.com:99999/Guide.docx",
                link_targets.Kind.EXTERNAL,
                id="a port out of range",
            ),
            pytest.param(
                "https://eur03.safelinks.protection.outlook.com/?url=https%3A%2F%2F"
                "example.sharepoint.com%3A99999%2FGuide.docx&data=05%7C02",
                link_targets.Kind.EXTERNAL,
                id="a port out of range behind a safe link",
            ),
            pytest.param(
                "file://share.example.gov.uk/user/Shared/Draft%20Letter",
                link_targets.Kind.NETWORK_SHARE,
                id="a network share",
            ),
            pytest.param(
                "#3-1-overview", link_targets.Kind.INTERNAL, id="a section of the guide"
            ),
        ],
    )
    def test_has_its_kind_and_no_canonical_link(self, link, kind):
        target = _target(link)

        assert target.kind == kind
        assert target.canonical_link is None
        assert target.file_name is None
