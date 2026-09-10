"""Errors raised while reading guidance documents."""


class DocumentParseError(Exception):
    """Raised when a source file cannot be read as a Word (.docx) package."""
