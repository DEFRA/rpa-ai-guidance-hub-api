"""Reading .docx bytes: what is accepted, and how every failure is reported."""

import io
import zipfile

import docx
import pytest

from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError

TITLE = "Example Grant Scheme Guide"


def _minimal_docx() -> bytes:
    """The smallest valid .docx, used where only the bytes matter."""
    buffer = io.BytesIO()
    docx.Document().save(buffer)
    return buffer.getvalue()


def _zip_that_is_not_an_office_package() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "no [Content_Types].xml here")
    return buffer.getvalue()


def _package_that_is_not_a_word_document() -> bytes:
    """A valid Office package whose main part claims to be a spreadsheet."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(_minimal_docx())) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for item in source.infolist():
            blob = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                blob = blob.replace(
                    b"wordprocessingml.document.main+xml",
                    b"spreadsheetml.sheet.main+xml",
                )
            target.writestr(item, blob)
    return buffer.getvalue()


class TestAReadableDocument:
    def test_a_document_that_is_all_cover_is_read_to_the_end(self, docx_bytes):
        """Nothing marks the end of the cover, so the last paragraph does."""

        def build(document):
            document.add_paragraph(TITLE, style="Title")

        assert parser.parse_docx(docx_bytes(build)).title == TITLE


class TestAnUnreadableSource:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(b"", "not a zip archive", id="empty"),
            pytest.param(
                _zip_that_is_not_an_office_package(),
                "not an Office package",
                id="zip-not-a-package",
            ),
            pytest.param(
                _package_that_is_not_a_word_document(),
                "not a Word document",
                id="not-word",
            ),
        ],
    )
    def test_every_kind_of_bad_input_raises_one_error_type(self, source, expected):
        """python-docx raises a different exception for each of these.

        BadZipFile, KeyError and ValueError respectively - and never the
        PackageNotFoundError its documentation suggests, which it only raises for a
        path. Callers should not have to know any of that, but they are still told
        which of the three went wrong.
        """
        with pytest.raises(DocumentParseError) as raised:
            parser.parse_docx(source)

        assert expected in str(raised.value)

    def test_the_underlying_failure_is_kept_as_the_cause(self):
        with pytest.raises(DocumentParseError) as raised:
            parser.parse_docx(b"")

        assert isinstance(raised.value.__cause__, zipfile.BadZipFile)

    def test_the_message_does_not_leak_the_stream_repr(self):
        """python-docx interpolates the BytesIO repr, which tells a caller nothing."""
        source = _package_that_is_not_a_word_document()

        with pytest.raises(DocumentParseError) as raised:
            parser.parse_docx(source)

        assert "BytesIO" not in str(raised.value)
