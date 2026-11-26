"""Word documents as uploaded, for tests that need the bytes of a real one."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING
from xml.sax import saxutils

import docx

if TYPE_CHECKING:
    from collections.abc import Callable


def minimal() -> bytes:
    """The smallest valid .docx, used where only the bytes matter."""
    buffer = io.BytesIO()
    docx.Document().save(buffer)
    return buffer.getvalue()


def with_part(name: str, rewrite: Callable[[bytes], bytes]) -> bytes:
    """The smallest valid .docx, with its part `name` rewritten."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(minimal())) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for item in source.infolist():
            blob = source.read(item.filename)
            target.writestr(item, rewrite(blob) if item.filename == name else blob)
    return buffer.getvalue()


def with_malformed_xml() -> bytes:
    """A .docx whose main part is not well-formed XML, though its package is sound."""
    return with_part("word/document.xml", lambda _blob: b"<broken")


_CUSTOM_PROPERTIES = (
    b'<Override PartName="/docProps/custom.xml" ContentType="application/'
    b'vnd.openxmlformats-officedocument.custom-properties+xml"/>'
)
_CUSTOM_PROPERTIES_RELATIONSHIP = (
    b'<Relationship Id="rIdCustom" Type="http://schemas.openxmlformats.org/'
    b'officeDocument/2006/relationships/custom-properties" '
    b'Target="docProps/custom.xml"/>'
)


def with_custom_property(name: str, value: str) -> bytes:
    """The smallest valid .docx, with one custom property, as SharePoint writes its
    own into a file in a library (`_dlc_DocIdUrl`)."""
    return with_custom_properties(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/'
        'officeDocument/2006/docPropsVTypes">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" '
        f'name="{name}"><vt:lpwstr>{saxutils.escape(value)}</vt:lpwstr></property>'
        "</Properties>"
    )


def with_custom_properties(part: str) -> bytes:
    """The smallest valid .docx, with `part` as its custom properties part."""
    added = {
        "[Content_Types].xml": lambda blob: blob.replace(
            b"</Types>", _CUSTOM_PROPERTIES + b"</Types>"
        ),
        "_rels/.rels": lambda blob: blob.replace(
            b"</Relationships>", _CUSTOM_PROPERTIES_RELATIONSHIP + b"</Relationships>"
        ),
    }
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(minimal())) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for item in source.infolist():
            blob = source.read(item.filename)
            target.writestr(item, added.get(item.filename, lambda b: b)(blob))
        target.writestr("docProps/custom.xml", part)
    return buffer.getvalue()
