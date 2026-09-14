from datetime import UTC, datetime

from app.guidance.documents.staging import models, schemas
from tests.fixtures import cdp_uploader


class TestUploadedDocument:
    def test_projects_the_completed_file(self):
        payload = schemas.UploadCallbackPayload.model_validate(
            cdp_uploader.cdp_callback_payload()
        )

        documents = payload.uploaded_documents()

        assert documents == [
            schemas.UploadedDocument(
                file_id="9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
                s3_key="scanned/3b0b2a02-a669-44ba-9b78-bd5cb8460253/9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
            )
        ]

    def test_ignores_non_file_form_fields(self):
        """`form.button` has no `fileId` and must not be mistaken for a file."""
        payload = schemas.UploadCallbackPayload.model_validate(
            cdp_uploader.cdp_callback_payload(
                extra_form_fields={"submit_button": "submit"}
            )
        )

        documents = payload.uploaded_documents()

        assert len(documents) == 1
        assert documents[0].file_id == "9fcaabe5-77ec-44db-8356-3a6e8dc51b13"

    def test_returns_empty_when_no_file_has_completed(self):
        body = cdp_uploader.cdp_callback_payload(
            cdp_uploader.uploaded_file_entry(file_status="pending")
        )
        payload = schemas.UploadCallbackPayload.model_validate(body)

        assert payload.uploaded_documents() == []

    def test_returns_empty_with_no_file_field_at_all(self):
        payload = schemas.UploadCallbackPayload.model_validate(
            {"uploadStatus": "ready", "form": {"button": "upload"}}
        )

        assert payload.uploaded_documents() == []

    def test_projects_every_completed_file_from_a_multi_file_field(self):
        """AC: multiple files are possible - a field's value can be a list, and
        each completed file gets its own document, keyed by its own file_id."""
        first = cdp_uploader.uploaded_file_entry(file_id="first")
        second = cdp_uploader.uploaded_file_entry(file_id="second")
        payload = schemas.UploadCallbackPayload.model_validate(
            cdp_uploader.cdp_callback_payload([first, second])
        )

        documents = payload.uploaded_documents()

        assert [document.file_id for document in documents] == ["first", "second"]

    def test_ignores_extra_fields_on_the_payload(self):
        """A future cdp-uploader field is not this endpoint's problem."""
        schemas.UploadCallbackPayload.model_validate(
            cdp_uploader.cdp_callback_payload(extra_attribute=123)
        )


class TestStagedDocumentResponse:
    def test_from_staged_document_maps_all_fields(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        last_modified = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        staged_document = models.StagedDocument(
            file_id="f-456",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="scanned/f-456.docx",
            created_at=created_at,
            updated_at=updated_at,
            title="Guidance Scheme B",
            version="2.0",
            last_modified=last_modified,
            parse_error=None,
        )

        response = schemas.StagedDocumentResponse.from_staged_document(staged_document)

        assert response.file_id == "f-456"
        assert response.parsing_status == models.ParsingStatus.COMPLETE
        assert response.title == "Guidance Scheme B"
        assert response.version == "2.0"
        assert response.last_modified == last_modified
        assert response.parsing_error is None

    def test_serializes_with_camel_case_aliases(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        staged_document = models.StagedDocument(
            file_id="f-789",
            parsing_status=models.ParsingStatus.FAILED,
            path="scanned/f-789.docx",
            created_at=created_at,
            updated_at=updated_at,
            parse_error="Invalid docx",
        )

        response = schemas.StagedDocumentResponse.from_staged_document(staged_document)
        dumped = response.model_dump(by_alias=True)

        assert dumped["fileId"] == "f-789"
        assert dumped["parsingStatus"] == "failed"
        assert dumped["parsingError"] == "Invalid docx"
        assert "file_id" not in dumped
