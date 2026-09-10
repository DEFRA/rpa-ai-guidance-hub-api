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
def service(fake_draft_store: Any, fake_s3: Any) -> tuple[DraftService, Any]:
    return DraftService(fake_draft_store, "a-bucket", fake_s3), fake_s3


class TestMultiFileUpload:
    async def test_both_files_are_claimed(self, service):
        draft_service, _ = service
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        assert await draft_service.handle_callback(first) is True
        assert await draft_service.handle_callback(second) is True

    async def test_a_repeat_callback_for_the_same_file_is_rejected(self, service):
        draft_service, _ = service
        document = UploadedDocument("first-file", "first.docx")

        assert await draft_service.handle_callback(document) is True
        assert await draft_service.handle_callback(document) is False

    async def test_each_file_is_parsed_and_recorded_under_its_own_id(
        self, service, monkeypatch: pytest.MonkeyPatch
    ):
        draft_service, _ = service
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        info_by_bytes = {
            b"first-bytes": MinimalDocumentInfo(title="First"),
            b"second-bytes": MinimalDocumentInfo(title="Second"),
        }
        monkeypatch.setattr(
            parser, "parse_minimal", lambda source: info_by_bytes[source]
        )

        await draft_service.handle_callback(first)
        await draft_service.handle_callback(second)
        await draft_service.minimal_parse(first)
        await draft_service.minimal_parse(second)

        first_status = await draft_service.get_status("first-file")
        second_status = await draft_service.get_status("second-file")

        assert first_status is not None
        assert first_status.title == "First"
        assert first_status.parsing_status == ParsingStatus.COMPLETE
        assert second_status is not None
        assert second_status.title == "Second"
        assert second_status.parsing_status == ParsingStatus.COMPLETE

    async def test_one_file_failing_to_parse_does_not_affect_the_other(
        self, service, monkeypatch: pytest.MonkeyPatch
    ):
        draft_service, _ = service
        first = UploadedDocument("first-file", "first.docx")
        second = UploadedDocument("second-file", "second.docx")

        def _parse_minimal(source: bytes) -> MinimalDocumentInfo:
            if source == b"first-bytes":
                reason = "not a Word document"
                raise DocumentParseError(reason)
            return MinimalDocumentInfo(title="Second")

        monkeypatch.setattr(parser, "parse_minimal", _parse_minimal)

        await draft_service.handle_callback(first)
        await draft_service.handle_callback(second)
        await draft_service.minimal_parse(first)
        await draft_service.minimal_parse(second)

        first_status = await draft_service.get_status("first-file")
        second_status = await draft_service.get_status("second-file")

        assert first_status is not None
        assert first_status.parsing_status == ParsingStatus.FAILED
        assert second_status is not None
        assert second_status.parsing_status == ParsingStatus.COMPLETE
