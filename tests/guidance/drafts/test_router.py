"""FastAPI draft router boundary tests (§1.2.1 Boundary Rule & §3.1 FastAPI).

Addresses the ASGI request boundary using `fastapi.testclient.TestClient`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.entrypoints.fastapi
from app.guidance.drafts import router as drafts_router
from app.guidance.drafts import store
from app.guidance.drafts.models import GuideDraft, ParsingStatus
from app.guidance.drafts.service import DraftService
from tests.fakes.draft_store import InMemoryDraftStore
from tests.fixtures.cdp_uploader import cdp_callback_payload, uploaded_file_entry


@pytest.fixture
def mock_store() -> InMemoryDraftStore:
    return InMemoryDraftStore()


@pytest.fixture
def mock_service(mock_store: InMemoryDraftStore) -> DraftService:
    s3_mock = MagicMock()
    return DraftService(mock_store, "test-bucket", s3_mock)


@pytest.fixture
def client(mock_service: DraftService, mock_store: InMemoryDraftStore):
    fastapi_app = app.entrypoints.fastapi.app
    fastapi_app.dependency_overrides[drafts_router.get_draft_service] = lambda: (
        mock_service
    )
    fastapi_app.dependency_overrides[drafts_router.get_draft_store] = lambda: mock_store
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


class TestHandleCallback:
    def test_enqueues_minimal_parse_when_file_claimed(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        mock_parse = AsyncMock()
        monkeypatch.setattr(DraftService, "minimal_parse", mock_parse)

        payload = cdp_callback_payload()

        response = client.post("/guidance/drafts/callback", json=payload)

        assert response.status_code == 202
        assert mock_parse.await_count == 1
        call_arg = mock_parse.await_args[0][0]
        assert call_arg.file_id == "9fcaabe5-77ec-44db-8356-3a6e8dc51b13"

    def test_returns_204_when_payload_has_no_completed_files(self, client):
        payload = cdp_callback_payload(uploaded_file_entry(file_status="pending"))

        response = client.post("/guidance/drafts/callback", json=payload)

        assert response.status_code == 204

    def test_skips_background_task_for_duplicate_or_already_claimed_file(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        mock_parse = AsyncMock()
        monkeypatch.setattr(DraftService, "minimal_parse", mock_parse)

        payload = cdp_callback_payload()

        first = client.post("/guidance/drafts/callback", json=payload)
        second = client.post("/guidance/drafts/callback", json=payload)

        assert first.status_code == 202
        assert second.status_code == 202
        # Only claimed once, so minimal_parse was enqueued exactly once
        assert mock_parse.await_count == 1

    def test_rejects_malformed_payload_with_422(self, client):
        response = client.post("/guidance/drafts/callback", json={"invalid": "payload"})

        assert response.status_code == 422

    def test_accepts_camel_case_cdp_uploader_contract(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        mock_parse = AsyncMock()
        monkeypatch.setattr(DraftService, "minimal_parse", mock_parse)

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

        response = client.post("/guidance/drafts/callback", json=body)

        assert response.status_code == 202


class TestGetDraft:
    def test_returns_draft_with_camel_case_fields_when_found(
        self, client, mock_store: InMemoryDraftStore
    ):
        last_modified = datetime(2026, 3, 4, 10, 0, 0, tzinfo=UTC)
        draft = GuideDraft(
            file_id="guide-123",
            parsing_status=ParsingStatus.COMPLETE,
            path="scanned/guide.docx",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            title="Grant Application Guide",
            version="1.5",
            last_modified=last_modified,
            parse_error=None,
        )
        mock_store.records["guide-123"] = draft

        response = client.get("/guidance/drafts/guide-123")

        assert response.status_code == 200
        data = response.json()
        assert data["fileId"] == "guide-123"
        assert data["parsingStatus"] == "complete"
        assert data["title"] == "Grant Application Guide"
        assert data["version"] == "1.5"
        assert data["lastModified"] == "2026-03-04T10:00:00Z"
        assert data["parsingError"] is None

    def test_returns_failed_draft_with_parsing_error(
        self, client, mock_store: InMemoryDraftStore
    ):
        draft = GuideDraft(
            file_id="guide-failed",
            parsing_status=ParsingStatus.FAILED,
            path="scanned/guide.docx",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parse_error="Invalid docx document",
        )
        mock_store.records["guide-failed"] = draft

        response = client.get("/guidance/drafts/guide-failed")

        assert response.status_code == 200
        data = response.json()
        assert data["fileId"] == "guide-failed"
        assert data["parsingStatus"] == "failed"
        assert data["parsingError"] == "Invalid docx document"

    def test_returns_404_when_draft_does_not_exist(self, client):
        response = client.get("/guidance/drafts/missing-guide-id")

        assert response.status_code == 404


class TestDraftDependencies:
    async def test_get_draft_store_and_service_wiring(self):
        mock_db = MagicMock()
        mock_db["guide_drafts"].create_index = AsyncMock()
        mock_s3 = MagicMock()

        draft_store = await drafts_router.get_draft_store(db=mock_db)
        assert isinstance(draft_store, store.MongoDraftStore)

        draft_service = await drafts_router.get_draft_service(
            draft_store=draft_store, s3_client=mock_s3
        )
        assert isinstance(draft_service, DraftService)
