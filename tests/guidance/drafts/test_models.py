from datetime import UTC, datetime

from app.guidance.drafts.models import GuideDraft, ParsingStatus


class TestGuideDraft:
    def test_round_trip_document_serialization(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        last_modified = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        draft = GuideDraft(
            file_id="draft-123",
            parsing_status=ParsingStatus.COMPLETE,
            path="scanned/draft.docx",
            created_at=created_at,
            updated_at=updated_at,
            title="Guidance Title",
            version="1.0.0",
            last_modified=last_modified,
            parse_error=None,
        )

        doc = draft.to_document()

        assert doc == {
            "_id": "draft-123",
            "parsing_status": "complete",
            "path": "scanned/draft.docx",
            "created_at": created_at,
            "updated_at": updated_at,
            "title": "Guidance Title",
            "version": "1.0.0",
            "last_modified": last_modified,
            "parse_error": None,
        }

        reconstituted = GuideDraft.from_document(doc)
        assert reconstituted == draft

    def test_from_document_handles_missing_optional_fields_as_none(self):
        created_at = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 9, 10, 12, 5, 0, tzinfo=UTC)
        doc = {
            "_id": "draft-bare",
            "parsing_status": "in_progress",
            "path": "scanned/bare.docx",
            "created_at": created_at,
            "updated_at": updated_at,
        }

        draft = GuideDraft.from_document(doc)

        assert draft.file_id == "draft-bare"
        assert draft.parsing_status == ParsingStatus.IN_PROGRESS
        assert draft.title is None
        assert draft.version is None
        assert draft.last_modified is None
        assert draft.parse_error is None
