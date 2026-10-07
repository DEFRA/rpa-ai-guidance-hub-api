"""FastAPI staging router boundary tests (§1.2.1 Boundary Rule & §3.1 FastAPI).

Addresses the ASGI request boundary using `fastapi.testclient.TestClient`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.documents.staging import models, router, service, store
from tests.fakes import staging_store as staging_store_fake
from tests.fixtures import cdp_uploader

DOCUMENT = uuid.UUID("0199b8a2-4c1e-7b3a-9d2f-6a1e3c5b7d90")
VERSION = uuid.UUID("0199b8a2-4c1f-7e21-8b4c-2f9a6d1e3b57")


@pytest.fixture
def mock_service(
    staging_store: staging_store_fake.InMemoryStagingStore, mocker
) -> service.StagingService:
    s3_mock = mocker.MagicMock()
    return service.StagingService(staging_store, "test-bucket", s3_mock)


@pytest.fixture
def client(
    mock_service: service.StagingService,
    staging_store: staging_store_fake.InMemoryStagingStore,
    mocker,
):
    # These tests exercise the router with fakes for storage, so the app
    # lifespan's real Mongo probe is stubbed out. Otherwise each fresh
    # `with TestClient(...)` here spins up its own event loop (via a new
    # anyio portal) and rebinds the session-wide real AsyncMongoClient to
    # it, breaking that client for every other test that touches it.
    mocker.patch("app.common.mongo.get_mongo_client", mocker.AsyncMock())
    mocker.patch(
        "app.common.mongo.get_db", mocker.AsyncMock(return_value=mocker.MagicMock())
    )
    mocker.patch("app.guidance.records.ensure_indexes", mocker.AsyncMock())

    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[router.get_staging_service] = lambda: mock_service
    fastapi_app.dependency_overrides[router.get_staging_store] = lambda: staging_store
    with fastapi.testclient.TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()
    fastapi_app.dependency_overrides.clear()


class TestHandleCallback:
    def test_enqueues_validate_and_parse_when_file_claimed(self, client, mocker):
        mock_parse = mocker.AsyncMock()
        mocker.patch.object(service.StagingService, "validate_and_parse", mock_parse)

        payload = cdp_uploader.cdp_callback_payload()

        response = client.post("/guides/staging/callback", json=payload)

        assert response.status_code == 202
        assert mock_parse.await_count == 1
        call_arg = mock_parse.await_args[0][0]
        assert call_arg.file_id == "9fcaabe5-77ec-44db-8356-3a6e8dc51b13"

    def test_returns_204_when_payload_has_no_completed_files(self, client):
        payload = cdp_uploader.cdp_callback_payload(
            cdp_uploader.uploaded_file_entry(file_status="pending")
        )

        response = client.post("/guides/staging/callback", json=payload)

        assert response.status_code == 204

    def test_skips_background_task_for_duplicate_or_already_claimed_file(
        self, client, mocker
    ):
        mock_parse = mocker.AsyncMock()
        mocker.patch.object(service.StagingService, "validate_and_parse", mock_parse)

        payload = cdp_uploader.cdp_callback_payload()

        first = client.post("/guides/staging/callback", json=payload)
        second = client.post("/guides/staging/callback", json=payload)

        assert first.status_code == 202
        assert second.status_code == 202
        # Only claimed once, so validate_and_parse was enqueued exactly once
        assert mock_parse.await_count == 1

    def test_rejects_malformed_payload_with_422(self, client):
        response = client.post("/guides/staging/callback", json={"invalid": "payload"})

        assert response.status_code == 422

    def test_accepts_camel_case_cdp_uploader_contract(self, client, mocker):
        mock_parse = mocker.AsyncMock()
        mocker.patch.object(service.StagingService, "validate_and_parse", mock_parse)

        body = {
            "uploadStatus": "ready",
            "form": {
                "document": {
                    "fileId": "file-1234",
                    "fileStatus": "complete",
                    "s3Key": "scanned/doc.docx",
                }
            },
        }

        response = client.post("/guides/staging/callback", json=body)

        assert response.status_code == 202


class TestGetStagedDocument:
    def test_returns_document_with_camel_case_fields_when_found(
        self, client, staging_store: staging_store_fake.InMemoryStagingStore
    ):
        last_modified = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        staged_document = models.StagedDocument(
            file_id="guide-123",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="scanned/guide.docx",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            title="Grant Application Guide",
            version="1.5",
            last_modified=last_modified,
            parse_error=None,
        )
        staging_store.records["guide-123"] = staged_document

        response = client.get("/guides/staging/guide-123")

        assert response.status_code == 200
        data = response.json()
        assert data["fileId"] == "guide-123"
        assert data["parsingStatus"] == "complete"
        assert data["title"] == "Grant Application Guide"
        assert data["version"] == "1.5"
        assert data["lastModified"] == "2026-03-04T10:00:00Z"
        assert data["parsingError"] is None

    def test_returns_failed_document_with_parsing_error(
        self, client, staging_store: staging_store_fake.InMemoryStagingStore
    ):
        staged_document = models.StagedDocument(
            file_id="guide-failed",
            parsing_status=models.ParsingStatus.FAILED,
            path="scanned/guide.docx",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parse_error="Invalid docx document",
        )
        staging_store.records["guide-failed"] = staged_document

        response = client.get("/guides/staging/guide-failed")

        assert response.status_code == 200
        data = response.json()
        assert data["fileId"] == "guide-failed"
        assert data["parsingStatus"] == "failed"
        assert data["parsingError"] == "Invalid docx document"

    def test_returns_the_document_converted_from_the_file_once_committed(
        self, client, staging_store: staging_store_fake.InMemoryStagingStore
    ):
        staging_store.records["guide-converted"] = models.StagedDocument(
            file_id="guide-converted",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="upload-1/guide-converted",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            document_id=DOCUMENT,
            version_id=VERSION,
            promoted_at=datetime(2026, 10, 6, 11, 0, 0, tzinfo=UTC),
        )

        response = client.get("/guides/staging/guide-converted")

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == str(DOCUMENT)
        assert data["promotedAt"] == "2026-10-06T11:00:00Z"

    def test_returns_no_commit_time_while_the_conversion_is_unfinished(
        self, client, staging_store: staging_store_fake.InMemoryStagingStore
    ):
        staging_store.records["guide-converting"] = models.StagedDocument(
            file_id="guide-converting",
            parsing_status=models.ParsingStatus.COMPLETE,
            path="upload-1/guide-converting",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            document_id=DOCUMENT,
            version_id=VERSION,
        )

        response = client.get("/guides/staging/guide-converting")

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == str(DOCUMENT)
        assert data["promotedAt"] is None

    def test_returns_404_when_document_does_not_exist(self, client):
        response = client.get("/guides/staging/missing-guide-id")

        assert response.status_code == 404


class TestStagingDependencies:
    def test_get_staging_store_and_service_wiring(self, mocker):
        mock_db = mocker.MagicMock()
        mock_s3 = mocker.MagicMock()

        staging_store = router.get_staging_store(db=mock_db)
        assert isinstance(staging_store, store.MongoStagingStore)

        staging_service = router.get_staging_service(
            staging_store=staging_store, s3_client=mock_s3
        )
        assert isinstance(staging_service, service.StagingService)
