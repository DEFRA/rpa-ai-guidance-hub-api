"""A document and its versions as the service holds them, and as Mongo stores them.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import dataclasses
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
    version_number="2.1",
    created_by_id="dev-user-123",
    created_by_display_name="Dev User",
    created_at=CREATED,
    upload_id="6f1d2c3b-1e8a-4c27-9b0d-3a5f7e9c1b24",
    file_id="7a2e9c41-5d3b-4f60-8e1a-2c4b6d8f0a13",
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
    def test_is_stored_with_its_upload_and_creator_nested(self):
        assert VERSION.to_document() == {
            "_id": "0199b8a2-0000-7000-8000-000000000002",
            "documentId": "0199b8a2-0000-7000-8000-000000000001",
            "contentUrl": "s3://docs/0199b8a2/0199b8a3/content.md",
            "title": "CS Revenue Claims Processing to Final Payment Guide",
            "versionNumber": "2.1",
            "createdBy": {"id": "dev-user-123", "displayName": "Dev User"},
            "createdAt": CREATED,
            "source": {
                "uploadId": "6f1d2c3b-1e8a-4c27-9b0d-3a5f7e9c1b24",
                "fileId": "7a2e9c41-5d3b-4f60-8e1a-2c4b6d8f0a13",
            },
        }

    def test_reads_back_as_it_was_stored(self):
        assert models.Version.from_document(VERSION.to_document()) == VERSION

    def test_one_stored_before_versions_kept_their_upload_reads_back_with_none(self):
        stored = VERSION.to_document()
        del stored["source"]

        assert models.Version.from_document(stored).file_id is None

    def test_one_stored_before_versions_had_numbers_reads_back_with_none(self):
        stored = VERSION.to_document()
        del stored["versionNumber"]

        assert models.Version.from_document(stored).version_number is None

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


class TestTheDetailsSearchIsGiven:
    def test_a_version_gives_its_own_details(self):
        version = dataclasses.replace(VERSION, metadata={"schemes": ["SFI"]})

        assert models.search_details(DOCUMENT, version) == {
            "schemeYear": 2026,
            "schemes": ["SFI"],
        }

    def test_a_version_saved_before_versions_kept_their_own_gives_its_documents(self):
        assert VERSION.metadata is None

        assert models.search_details(DOCUMENT, VERSION) == {
            "schemeYear": 2026,
            **DOCUMENT.metadata,
        }

    def test_a_version_with_empty_details_does_not_fall_back_to_its_documents(self):
        version = dataclasses.replace(VERSION, metadata={})

        assert models.search_details(DOCUMENT, version) == {"schemeYear": 2026}

    def test_details_are_given_as_they_are_read_now(self):
        version = dataclasses.replace(
            VERSION,
            metadata={"schemes": [models.NOT_SCHEME_SPECIFIC], "schemeYear": 2027},
        )

        assert models.search_details(DOCUMENT, version) == {
            "schemes": [],
            "schemeYear": 2027,
        }

    def test_neither_having_details_gives_only_the_scheme_year(self):
        document = dataclasses.replace(DOCUMENT, metadata=None)

        assert models.search_details(document, VERSION) == {"schemeYear": 2026}
