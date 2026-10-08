from datetime import UTC, datetime

from app.guidance.documents.staging import models


class TestStagedDocument:
    def test_round_trip_document_serialization(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        last_modified = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        staged_document = models.StagedDocument(
            file_id="staged-123",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="scanned/staged.docx",
            created_at=created_at,
            updated_at=updated_at,
            title="Guidance Title",
            version="1.0.0",
            last_modified=last_modified,
            parse_error=None,
        )

        doc = staged_document.to_document()

        assert doc == {
            "_id": "staged-123",
            "parsing_status": "complete",
            "path": "scanned/staged.docx",
            "created_at": created_at,
            "updated_at": updated_at,
            "title": "Guidance Title",
            "version": "1.0.0",
            "last_modified": last_modified,
            "parse_error": None,
            "document_id": None,
            "version_id": None,
            "promoted_at": None,
            "saving_status": None,
            "save_steps_completed": None,
            "save_steps_total": None,
            "save_error": None,
        }

        reconstituted = models.StagedDocument.from_document(doc)
        assert reconstituted == staged_document

    def test_from_document_handles_missing_optional_fields_as_none(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        doc = {
            "_id": "staged-bare",
            "parsing_status": "in_progress",
            "path": "scanned/bare.docx",
            "created_at": created_at,
            "updated_at": updated_at,
        }

        staged_document = models.StagedDocument.from_document(doc)

        assert staged_document.file_id == "staged-bare"
        assert staged_document.parsing_status == models.ParsingStatus.IN_PROGRESS
        assert staged_document.title is None
        assert staged_document.version is None
        assert staged_document.last_modified is None
        assert staged_document.parse_error is None

    def test_how_far_saving_has_got_reads_back_as_it_was_stored(self):
        created_at = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
        staged_document = models.StagedDocument(
            file_id="staged-saving",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="upload/staged-saving",
            created_at=created_at,
            updated_at=created_at,
            saving_status=models.SavingStatus.IN_PROGRESS,
            save_steps_completed=12,
            save_steps_total=74,
        )

        doc = staged_document.to_document()

        assert doc["saving_status"] == "in_progress"
        assert (doc["save_steps_completed"], doc["save_steps_total"]) == (12, 74)
        assert models.StagedDocument.from_document(doc) == staged_document

    def test_a_record_from_before_saving_was_tracked_reads_back_with_none(self):
        created_at = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
        doc = {
            "_id": "staged-old",
            "parsing_status": "complete",
            "path": "upload/staged-old",
            "created_at": created_at,
            "updated_at": created_at,
        }

        staged_document = models.StagedDocument.from_document(doc)

        assert staged_document.saving_status is None
        assert staged_document.save_steps_completed is None
        assert staged_document.save_error is None
