"""`parser.parse_minimal`: title, version and last-modified, however many of the
three a document actually carries.

Title reuses `_extract_title`, which `test_title.py` already covers exhaustively -
these cases only check that `parse_minimal` wires it in, not the heuristic itself.
"""

from datetime import UTC, datetime

import pytest

from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError
from tests.fixtures import word_documents

TITLE = "Example Grant Scheme Guide"
VERSION = "2.1"
MODIFIED = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)
DOCUMENT_ID_LINK = (
    "https://example.sharepoint.com/teams/Team1234/_layouts/15/DocIdRedir.aspx"
    "?ID=RPAINTRA-405-3434"
)


class TestParseMinimal:
    def test_extracts_every_field_a_document_carries(self, docx_bytes):
        def build(document):
            document.add_paragraph(TITLE, style="Title")
            document.core_properties.version = VERSION
            document.core_properties.modified = MODIFIED

        info = parser.parse_minimal(docx_bytes(build))

        assert info.title == TITLE
        assert info.version == VERSION
        assert info.last_modified == MODIFIED

    def test_a_field_the_document_never_set_comes_back_empty(self, docx_bytes):
        """AC3: no field missing from the file fails the whole parse."""
        info = parser.parse_minimal(docx_bytes())

        assert info.title == ""
        assert info.version == ""

    def test_falls_back_to_the_docprops_title_with_no_cover_title(self, docx_bytes):
        info = parser.parse_minimal(docx_bytes(core_title=TITLE))

        assert info.title == TITLE

    def test_reads_the_document_id_link_sharepoint_wrote_into_the_file(self):
        """Its value is the link, a comma, then the ID."""
        source = word_documents.with_custom_property(
            "_dlc_DocIdUrl", f"{DOCUMENT_ID_LINK}, RPAINTRA-405-3434"
        )

        assert parser.parse_minimal(source).document_id_link == DOCUMENT_ID_LINK

    def test_a_file_whose_custom_properties_cannot_be_read_has_none(self):
        """They are SharePoint's, not the author's: the file is still read."""
        source = word_documents.with_custom_properties("<broken")

        assert parser.parse_minimal(source).document_id_link is None

    def test_a_file_whose_custom_properties_are_outside_it_has_none(self):
        source = word_documents.with_external_custom_properties()

        assert parser.parse_minimal(source).document_id_link is None

    def test_a_file_with_no_document_id_link_has_none(self, docx_bytes):
        assert parser.parse_minimal(docx_bytes()).document_id_link is None

    def test_raises_document_parse_error_for_a_file_that_is_not_a_docx(self):
        """AC7: unreadable is the only failure mode - not a missing field."""
        with pytest.raises(DocumentParseError):
            parser.parse_minimal(b"not a docx file")
