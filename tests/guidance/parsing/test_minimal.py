"""`parser.parse_minimal`: title, version and last-modified, however many of the
three a document actually carries.

Title reuses `_extract_title`, which `test_title.py` already covers exhaustively -
these cases only check that `parse_minimal` wires it in, not the heuristic itself.
"""

from datetime import UTC, datetime

import pytest

from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError

TITLE = "Example Grant Scheme Guide"
VERSION = "2.1"
MODIFIED = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)


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

    def test_raises_document_parse_error_for_a_file_that_is_not_a_docx(self):
        """AC7: unreadable is the only failure mode - not a missing field."""
        with pytest.raises(DocumentParseError):
            parser.parse_minimal(b"not a docx file")
