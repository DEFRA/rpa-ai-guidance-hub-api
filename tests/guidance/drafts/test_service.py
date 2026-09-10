from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.guidance.drafts.models import ParsingStatus
from app.guidance.drafts.schemas import UploadedDocument
from app.guidance.drafts.service import DraftService
from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError
from app.guidance.parsing.models import MinimalDocumentInfo
from tests.fakes.draft_store import InMemoryDraftStore


@pytest.fixture
def fake_s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        client.create_bucket(
            Bucket="a-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        client.put_object(Bucket="a-bucket", Key="first.docx", Body=b"first-bytes")
        client.put_object(Bucket="a-bucket", Key="second.docx", Body=b"second-bytes")
        yield client


@pytest.fixture
def store() -> InMemoryDraftStore:
    return InMemoryDraftStore()


@pytest.fixture
def service(store: InMemoryDraftStore, fake_s3: Any) -> DraftService:
    return DraftService(store, "a-bucket", fake_s3)


class TestHandleCallback:
    async def test_claims_unprocessed_document(self, service):
        document = UploadedDocument("file-1", "first.docx")

        assert await service.handle_callback(document) is True

    async def test_rejects_already_claimed_document(self, service):
        document = UploadedDocument("file-1", "first.docx")

        assert await service.handle_callback(document) is True
        assert await service.handle_callback(document) is False


class TestMinimalParse:
    async def test_parses_bytes_from_s3_and_marks_complete(
        self, service, store, monkeypatch: pytest.MonkeyPatch
    ):
        document = UploadedDocument("file-1", "first.docx")
        info = MinimalDocumentInfo(title="Parsed Title", version="1.0")
        monkeypatch.setattr(parser, "parse_minimal", lambda _source: info)

        await service.handle_callback(document)
        await service.minimal_parse(document)

        draft = await store.get("file-1")
        assert draft is not None
        assert draft.parsing_status == ParsingStatus.COMPLETE
        assert draft.title == "Parsed Title"
        assert draft.version == "1.0"

    async def test_records_failure_when_document_parse_error_occurs(
        self, service, store, monkeypatch: pytest.MonkeyPatch
    ):
        document = UploadedDocument("file-corrupt", "first.docx")

        def _raise_parse_error(_source: bytes) -> MinimalDocumentInfo:
            msg = "corrupted zip archive"
            raise DocumentParseError(msg)

        monkeypatch.setattr(parser, "parse_minimal", _raise_parse_error)

        await service.handle_callback(document)
        await service.minimal_parse(document)

        draft = await store.get("file-corrupt")
        assert draft is not None
        assert draft.parsing_status == ParsingStatus.FAILED
        assert draft.parse_error == "corrupted zip archive"


class TestMultiFileUpload:
    async def test_both_files_are_claimed(self, service):
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        assert await service.handle_callback(first) is True
        assert await service.handle_callback(second) is True

    async def test_each_file_is_parsed_and_recorded_under_its_own_id(
        self, service, monkeypatch: pytest.MonkeyPatch
    ):
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        info_by_bytes = {
            b"first-bytes": MinimalDocumentInfo(title="First"),
            b"second-bytes": MinimalDocumentInfo(title="Second"),
        }
        monkeypatch.setattr(
            parser, "parse_minimal", lambda source: info_by_bytes[source]
        )

        await service.handle_callback(first)
        await service.handle_callback(second)
        await service.minimal_parse(first)
        await service.minimal_parse(second)

        first_status = await service.get_draft("first-file")
        second_status = await service.get_draft("second-file")

        assert first_status is not None
        assert first_status.title == "First"
        assert first_status.parsing_status == ParsingStatus.COMPLETE
        assert second_status is not None
        assert second_status.title == "Second"
        assert second_status.parsing_status == ParsingStatus.COMPLETE

    async def test_one_file_failing_to_parse_does_not_affect_the_other(
        self, service, monkeypatch: pytest.MonkeyPatch
    ):
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        def _parse_minimal(source: bytes) -> MinimalDocumentInfo:
            if source == b"first-bytes":
                reason = "not a Word document"
                raise DocumentParseError(reason)
            return MinimalDocumentInfo(title="Second")

        monkeypatch.setattr(parser, "parse_minimal", _parse_minimal)

        await service.handle_callback(first)
        await service.handle_callback(second)
        await service.minimal_parse(first)
        await service.minimal_parse(second)

        first_status = await service.get_draft("first-file")
        second_status = await service.get_draft("second-file")

        assert first_status is not None
        assert first_status.parsing_status == ParsingStatus.FAILED
        assert second_status is not None
        assert second_status.parsing_status == ParsingStatus.COMPLETE


class TestGetDraft:
    async def test_returns_draft_from_store(self, service):
        first = UploadedDocument("first-file", "first.docx")
        await service.handle_callback(first)

        draft = await service.get_draft("first-file")

        assert draft is not None
        assert draft.file_id == "first-file"

    async def test_returns_none_when_store_has_no_draft(self, service):
        draft = await service.get_draft("missing-id")

        assert draft is None
