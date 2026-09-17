"""FastAPI staging router boundary tests (§1.2.1 Boundary Rule & §3.1 FastAPI).

Addresses the ASGI request boundary using `fastapi.testclient.TestClient`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.documents.staging import models, router, service, store
from tests.fakes import staging_store as staging_store_fake
from tests.fixtures import cdp_uploader


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
):
    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[router.get_staging_service] = lambda: mock_service
    fastapi_app.dependency_overrides[router.get_staging_store] = lambda: staging_store
    # Deliberately not entered as a context manager, so the app's lifespan does not
    # run: it connects to MongoDB and makes indexes, on whatever loop TestClient
    # brings, while the session-scoped test client is bound to the session's. These
    # cases are about the endpoint rather than about the service coming up.
    test_client = fastapi.testclient.TestClient(fastapi_app)
    yield test_client
    fastapi_app.dependency_overrides.clear()


class TestHandleCallback:
    def test_enqueues_minimal_parse_when_file_claimed(self, client, mocker):
        mock_parse = mocker.AsyncMock()
        mocker.patch.object(service.StagingService, "minimal_parse", mock_parse)

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
        mocker.patch.object(service.StagingService, "minimal_parse", mock_parse)

        payload = cdp_uploader.cdp_callback_payload()

        first = client.post("/guides/staging/callback", json=payload)
        second = client.post("/guides/staging/callback", json=payload)

        assert first.status_code == 202
        assert second.status_code == 202
        # Only claimed once, so minimal_parse was enqueued exactly once
        assert mock_parse.await_count == 1

    def test_rejects_malformed_payload_with_422(self, client):
        response = client.post("/guides/staging/callback", json={"invalid": "payload"})

        assert response.status_code == 422

    def test_accepts_camel_case_cdp_uploader_contract(self, client, mocker):
        mock_parse = mocker.AsyncMock()
        mocker.patch.object(service.StagingService, "minimal_parse", mock_parse)

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
