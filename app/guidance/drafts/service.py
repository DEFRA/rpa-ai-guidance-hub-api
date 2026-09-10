from __future__ import annotations

import asyncio
from logging import getLogger
from typing import Any

from app.guidance.drafts import store
from app.guidance.drafts.models import GuideDraft
from app.guidance.drafts.schemas import UploadedDocument
from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError

logger = getLogger(__name__)


class DraftService:
    def __init__(
        self,
        draft_store: store.DraftStore,
        bucket_name: str,
        s3_client: Any,
    ) -> None:
        self._store = draft_store
        self._bucket = bucket_name
        self._s3 = s3_client

    async def handle_callback(self, document: UploadedDocument) -> bool:
        return await self._store.claim(document.file_id, document.s3_key)

    async def minimal_parse(self, document: UploadedDocument) -> None:
        doc_bytes = await self._get_object_bytes(document)

        try:
            info = parser.parse_minimal(doc_bytes)
        except DocumentParseError as exc:
            logger.warning("Failed to parse file %s: %s", document.file_id, exc)
            await self._store.mark_failed(document.file_id, str(exc))
            return

        await self._store.mark_complete(document.file_id, info)

    async def get_draft(self, file_id: str) -> GuideDraft | None:
        return await self._store.get(file_id)

    async def _get_object_bytes(self, document: UploadedDocument) -> bytes:
        def _fetch() -> bytes:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=document.s3_key,
            )

            return response["Body"].read()  # type: ignore[no-any-return]

        return await asyncio.to_thread(_fetch)
