from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pydantic
from pydantic.alias_generators import to_camel

from app.guidance.drafts import models

# The only fileStatus a completed scan lands on - cdp-uploader's "clean and
# copied to the destination bucket" answer. Anything else (pending, rejected)
# has no S3 object yet worth parsing.
_FILE_STATUS_COMPLETE = "complete"


@dataclass(frozen=True)
class UploadedDocument:
    file_id: str
    s3_key: str


class UploadedFile(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    file_id: str = pydantic.Field(validation_alias="fileId")
    file_status: str = pydantic.Field(validation_alias="fileStatus")
    s3_key: str = pydantic.Field(validation_alias="s3Key")


class UploadCallbackPayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    upload_status: str = pydantic.Field(validation_alias="uploadStatus")
    form: dict[str, Any] = pydantic.Field(default_factory=dict)

    def uploaded_documents(self) -> list[UploadedDocument]:
        documents: list[UploadedDocument] = []

        for value in self.form.values():
            entries = value if isinstance(value, list) else [value]

            for entry in entries:
                if not isinstance(entry, dict) or "fileId" not in entry:
                    continue

                file = UploadedFile.model_validate(entry)
                if file.file_status == _FILE_STATUS_COMPLETE:
                    documents.append(
                        UploadedDocument(
                            file_id=file.file_id,
                            s3_key=file.s3_key,
                        )
                    )

        return documents


class DraftResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(alias_generator=to_camel, populate_by_name=True)

    file_id: str
    parsing_status: models.ParsingStatus
    parsing_error: str | None = None
    title: str | None = None
    version: str | None = None
    last_modified: datetime | None = None

    @classmethod
    def from_guide_draft(cls, draft: models.GuideDraft) -> DraftResponse:
        return cls(
            file_id=draft.file_id,
            parsing_status=draft.parsing_status,
            parsing_error=draft.parse_error,
            title=draft.title,
            version=draft.version,
            last_modified=draft.last_modified,
        )
