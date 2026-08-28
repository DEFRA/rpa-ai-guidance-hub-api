"""Shared fixtures for the parsing tests.

All fixture text in this package is invented. No real guidance document, scheme or
organisation name appears anywhere in the suite - keep it that way when adding cases.
"""

import io

import docx
import pytest


@pytest.fixture
def docx_bytes():
    """Return a builder for an in-memory .docx.

    Takes a callable that populates the document, so a test can use the whole of
    python-docx's API rather than whatever vocabulary a declarative fixture would
    have to invent. `core_title` sets the docProps title, which is separate from
    anything printed on the page.
    """

    def build_docx(build=None, core_title=None) -> bytes:
        document = docx.Document()
        if build is not None:
            build(document)
        if core_title is not None:
            document.core_properties.title = core_title

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    return build_docx
