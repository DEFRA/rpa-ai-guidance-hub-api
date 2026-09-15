from __future__ import annotations

import asyncio
from logging import getLogger
from typing import Any

from app.guidance.documents.staging import models, schemas, store
from app.guidance.parsing import errors, parser

logger = getLogger(__name__)


class StagingService:
    def __init__(
        self,
        staging_store: store.StagingStore,
        bucket_name: str,
        s3_client: Any,
    ) -> None:
        self._store = staging_store
        self._bucket = bucket_name
        self._s3 = s3_client

    async def handle_callback(self, document: schemas.UploadedDocument) -> bool:
        return await self._store.claim(document.file_id, document.s3_key)

    async def minimal_parse(self, document: schemas.UploadedDocument) -> None:
        doc_bytes = await self._get_object_bytes(document)

        try:
            info = parser.parse_minimal(doc_bytes)
        except errors.DocumentParseError as exc:
            logger.warning("Failed to parse file %s: %s", document.file_id, exc)
            await self._store.mark_failed(document.file_id, str(exc))
            return

        await self._store.mark_complete(document.file_id, info)

    async def get_staged_doc(self, file_id: str) -> models.StagedDocument | None:
        return await self._store.get(file_id)

    async def _get_object_bytes(self, document: schemas.UploadedDocument) -> bytes:
        def _fetch() -> bytes:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=document.s3_key,
            )

            return response["Body"].read()  # type: ignore[no-any-return]

        return await asyncio.to_thread(_fetch)
