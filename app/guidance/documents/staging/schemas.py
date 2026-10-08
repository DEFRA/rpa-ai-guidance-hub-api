from __future__ import annotations

from datetime import datetime
from typing import Any

import pydantic
from pydantic.alias_generators import to_camel

from app.guidance.documents.staging import models
from app.guidance.ids import DocumentId, FileId

# The only fileStatus a completed scan lands on - cdp-uploader's "clean and
# copied to the destination bucket" answer. Anything else (pending, rejected)
# has no S3 object yet worth parsing.
_FILE_STATUS_COMPLETE = "complete"


class UploadedFile(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    file_id: FileId = pydantic.Field(validation_alias="fileId")
    file_status: str = pydantic.Field(validation_alias="fileStatus")
    s3_key: str = pydantic.Field(validation_alias="s3Key")


class UploadCallbackPayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    upload_status: str = pydantic.Field(validation_alias="uploadStatus")
    form: dict[str, Any] = pydantic.Field(default_factory=dict)

    def uploaded_documents(self) -> list[models.UploadedDocument]:
        documents: list[models.UploadedDocument] = []

        for value in self.form.values():
            entries = value if isinstance(value, list) else [value]

            for entry in entries:
                if not isinstance(entry, dict) or "fileId" not in entry:
                    continue

                file = UploadedFile.model_validate(entry)
                if file.file_status == _FILE_STATUS_COMPLETE:
                    documents.append(
                        models.UploadedDocument(
                            file_id=file.file_id,
                            s3_key=file.s3_key,
                        )
                    )

        return documents


class StagedDocumentResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(alias_generator=to_camel, populate_by_name=True)

    file_id: FileId
    parsing_status: models.ParsingStatus
    parsing_error: str | None = None
    title: str | None = None
    version: str | None = None
    last_modified: datetime | None = None
    # The document this file was converted into, and when it was committed: set
    # once the conversion finishes, so a caller that gave up waiting on POST
    # /guides can tell whether it finished anyway.
    document_id: DocumentId | None = None
    promoted_at: datetime | None = None
    # Saving that document, once started: how many of its steps are done out of
    # how many (a write per picture, then the Markdown), and why it failed if it did.
    saving_status: models.SavingStatus | None = None
    save_steps_completed: int | None = None
    save_steps_total: int | None = None
    save_error: str | None = None

    @classmethod
    def from_staged_document(
        cls, staged_document: models.StagedDocument
    ) -> StagedDocumentResponse:
        return cls(
            file_id=staged_document.file_id,
            parsing_status=staged_document.parsing_status,
            parsing_error=staged_document.parse_error,
            title=staged_document.title,
            version=staged_document.version,
            last_modified=staged_document.last_modified,
            document_id=staged_document.document_id,
            promoted_at=staged_document.promoted_at,
            saving_status=staged_document.saving_status,
            save_steps_completed=staged_document.save_steps_completed,
            save_steps_total=staged_document.save_steps_total,
            save_error=staged_document.save_error,
        )
