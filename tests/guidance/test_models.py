"""A document and its versions as the service holds them, and as Mongo stores them.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import datetime as dt

from app.guidance import models

CREATED = dt.datetime(2026, 10, 7, 9, 30, tzinfo=dt.UTC)

DOCUMENT = models.Document(
    id="0199b8a2-0000-7000-8000-000000000001",
    upload_id="854a1f43-aab4-4579-b166-d708c0aad436",
    file_id="bf9b3179-47d0-4104-9944-5e23421ef437",
    filename="CS Revenue Claims.docx",
    metadata={"title": "CS Revenue Claims", "owners": ["Revenue Claims Team"]},
    created_at=CREATED,
)

VERSION = models.Version(
    id="0199b8a2-0000-7000-8000-000000000002",
    document_id="0199b8a2-0000-7000-8000-000000000001",
    content_url="s3://docs/0199b8a2/0199b8a3/content.md",
    title="CS Revenue Claims Processing to Final Payment Guide",
    created_by_id="dev-user-123",
    created_by_display_name="Dev User",
    created_at=CREATED,
)


class TestADocumentAsStored:
    def test_is_stored_with_its_upload_nested_as_its_source(self):
        assert DOCUMENT.to_document() == {
            "_id": "0199b8a2-0000-7000-8000-000000000001",
            "metadata": {
                "title": "CS Revenue Claims",
                "owners": ["Revenue Claims Team"],
            },
            "source": {
                "uploadId": "854a1f43-aab4-4579-b166-d708c0aad436",
                "fileId": "bf9b3179-47d0-4104-9944-5e23421ef437",
                "filename": "CS Revenue Claims.docx",
            },
            "createdAt": CREATED,
        }

    def test_reads_back_as_it_was_stored(self):
        assert models.Document.from_document(DOCUMENT.to_document()) == DOCUMENT

    def test_a_source_with_no_filename_reads_back_with_none(self):
        stored = DOCUMENT.to_document()
        del stored["source"]["filename"]

        assert models.Document.from_document(stored).filename is None


class TestAVersionAsStored:
    def test_is_stored_with_who_made_it_nested_as_its_creator(self):
        assert VERSION.to_document() == {
            "_id": "0199b8a2-0000-7000-8000-000000000002",
            "documentId": "0199b8a2-0000-7000-8000-000000000001",
            "contentUrl": "s3://docs/0199b8a2/0199b8a3/content.md",
            "title": "CS Revenue Claims Processing to Final Payment Guide",
            "createdBy": {"id": "dev-user-123", "displayName": "Dev User"},
            "createdAt": CREATED,
        }

    def test_reads_back_as_it_was_stored(self):
        assert models.Version.from_document(VERSION.to_document()) == VERSION

    def test_a_version_made_by_nobody_is_stored_as_made_by_nobody(self):
        """Not an empty creator: no one at all, which is what an unauthenticated
        request is."""
        anonymous = models.Version(
            id=VERSION.id,
            document_id=VERSION.document_id,
            content_url=VERSION.content_url,
            created_at=CREATED,
        )

        assert anonymous.to_document()["createdBy"] is None
        assert models.Version.from_document(anonymous.to_document()) == anonymous
