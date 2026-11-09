"""Word documents as uploaded, for tests that need the bytes of a real one."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

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
