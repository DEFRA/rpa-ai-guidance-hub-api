from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.guidance.documents.staging import models, schemas, service
from app.guidance.parsing import errors, parser
from app.guidance.parsing import models as parsing_models
from tests.fakes import staging_store as staging_store_fake


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
def staging_service(
    staging_store: staging_store_fake.InMemoryStagingStore, fake_s3: Any
) -> service.StagingService:
    return service.StagingService(staging_store, "a-bucket", fake_s3)


class TestHandleCallback:
    async def test_claims_unprocessed_document(self, staging_service):
        document = schemas.UploadedDocument("file-1", "first.docx")

        assert await staging_service.handle_callback(document) is True

    async def test_rejects_already_claimed_document(self, staging_service):
        document = schemas.UploadedDocument("file-1", "first.docx")

        assert await staging_service.handle_callback(document) is True
        assert await staging_service.handle_callback(document) is False


class TestValidateAndParse:
    async def test_parses_bytes_from_s3_and_marks_complete(
        self,
        staging_service,
        staging_store: staging_store_fake.InMemoryStagingStore,
        monkeypatch: pytest.MonkeyPatch,
    ):
        document = schemas.UploadedDocument("file-1", "first.docx")
        info = parsing_models.MinimalDocumentInfo(title="Parsed Title", version="1.0")
        monkeypatch.setattr(parser, "parse_docx", lambda _source: None)
        monkeypatch.setattr(parser, "parse_minimal", lambda _source: info)

        await staging_service.handle_callback(document)
        await staging_service.validate_and_parse(document)

        staged_document = await staging_store.get("file-1")
        assert staged_document is not None
        assert staged_document.parsing_status == models.ParsingStatus.COMPLETE
        assert staged_document.title == "Parsed Title"
        assert staged_document.version == "1.0"

    async def test_records_failure_when_document_cannot_be_fully_parsed(
        self,
        staging_service,
        staging_store: staging_store_fake.InMemoryStagingStore,
        monkeypatch: pytest.MonkeyPatch,
    ):
        document = schemas.UploadedDocument("file-corrupt", "first.docx")
        parse_minimal_calls: list[bytes] = []

        def _raise_parse_error(_source: bytes) -> parsing_models.MarkdownDocument:
            msg = "corrupted zip archive"
            raise errors.DocumentParseError(msg)

        monkeypatch.setattr(parser, "parse_docx", _raise_parse_error)
        monkeypatch.setattr(
            parser, "parse_minimal", lambda source: parse_minimal_calls.append(source)
        )

        await staging_service.handle_callback(document)
        await staging_service.validate_and_parse(document)

        staged_document = await staging_store.get("file-corrupt")
        assert staged_document is not None
        assert staged_document.parsing_status == models.ParsingStatus.FAILED
        assert staged_document.parse_error == "corrupted zip archive"
        # Metadata extraction must not run against a document that failed validation.
        assert parse_minimal_calls == []


class TestMultiFileUpload:
    async def test_both_files_are_claimed(self, staging_service):
        first = schemas.UploadedDocument("first-file", "first.docx")
        second = schemas.UploadedDocument("second-file", "second.docx")

        assert await staging_service.handle_callback(first) is True
        assert await staging_service.handle_callback(second) is True

    async def test_each_file_is_parsed_and_recorded_under_its_own_id(
        self, staging_service, monkeypatch: pytest.MonkeyPatch
    ):
        first = schemas.UploadedDocument("first-file", "first.docx")
        second = schemas.UploadedDocument("second-file", "second.docx")

        info_by_bytes = {
            b"first-bytes": parsing_models.MinimalDocumentInfo(title="First"),
            b"second-bytes": parsing_models.MinimalDocumentInfo(title="Second"),
        }
        monkeypatch.setattr(parser, "parse_docx", lambda _source: None)
        monkeypatch.setattr(
            parser, "parse_minimal", lambda source: info_by_bytes[source]
        )

        await staging_service.handle_callback(first)
        await staging_service.handle_callback(second)
        await staging_service.validate_and_parse(first)
        await staging_service.validate_and_parse(second)

        first_status = await staging_service.get_staged_doc("first-file")
        second_status = await staging_service.get_staged_doc("second-file")

        assert first_status is not None
        assert first_status.title == "First"
        assert first_status.parsing_status == models.ParsingStatus.COMPLETE
        assert second_status is not None
        assert second_status.title == "Second"
        assert second_status.parsing_status == models.ParsingStatus.COMPLETE

    async def test_one_file_failing_to_validate_does_not_affect_the_other(
        self, staging_service, monkeypatch: pytest.MonkeyPatch
    ):
        first = schemas.UploadedDocument("first-file", "first.docx")
        second = schemas.UploadedDocument("second-file", "second.docx")

        def _parse_docx(source: bytes) -> parsing_models.MarkdownDocument | None:
            if source == b"first-bytes":
                reason = "not a Word document"
                raise errors.DocumentParseError(reason)
            return None

        monkeypatch.setattr(parser, "parse_docx", _parse_docx)
        monkeypatch.setattr(
            parser,
            "parse_minimal",
            lambda _source: parsing_models.MinimalDocumentInfo(title="Second"),
        )

        await staging_service.handle_callback(first)
        await staging_service.handle_callback(second)
        await staging_service.validate_and_parse(first)
        await staging_service.validate_and_parse(second)

        first_status = await staging_service.get_staged_doc("first-file")
        second_status = await staging_service.get_staged_doc("second-file")

        assert first_status is not None
        assert first_status.parsing_status == models.ParsingStatus.FAILED
        assert second_status is not None
        assert second_status.parsing_status == models.ParsingStatus.COMPLETE


class TestGetStagedDoc:
    async def test_returns_document_from_store(self, staging_service):
        first = schemas.UploadedDocument("first-file", "first.docx")
        await staging_service.handle_callback(first)

        staged_document = await staging_service.get_staged_doc("first-file")

        assert staged_document is not None
        assert staged_document.file_id == "first-file"

    async def test_returns_none_when_store_has_no_document(self, staging_service):
        staged_document = await staging_service.get_staged_doc("missing-id")

        assert staged_document is None
