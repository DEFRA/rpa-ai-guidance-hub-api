from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any

import pydantic
import pydantic.alias_generators

from app.guidance import ids
from app.guidance.documents.staging import models

logger = logging.getLogger(__name__)

# The only fileStatus a completed scan lands on - cdp-uploader's "clean and
# copied to the destination bucket" answer. Only these files carry an s3Key;
# rejected ones (virus, wrong type...) and anything else have no S3 object.
_FILE_STATUS_COMPLETE = "complete"
_COMPLETE_TAG = "complete"
_UNFINISHED_TAG = "unfinished"


class CompletedFile(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    file_id: ids.FileId = pydantic.Field(validation_alias="fileId")
    s3_key: str = pydantic.Field(validation_alias="s3Key", min_length=1)


class UnfinishedFile(pydantic.BaseModel):
    """A file that has no S3 object: rejected (see `error_code`) or pending."""

    model_config = pydantic.ConfigDict(extra="ignore")

    file_id: ids.FileId = pydantic.Field(validation_alias="fileId")
    file_status: str | None = pydantic.Field(
        default=None, validation_alias="fileStatus"
    )
    error_code: str | None = pydantic.Field(default=None, validation_alias="errorCode")


def _file_tag(value: Any) -> str:
    status = value.get("fileStatus") if isinstance(value, dict) else value.file_status
    return _COMPLETE_TAG if status == _FILE_STATUS_COMPLETE else _UNFINISHED_TAG


# s3Key is required exactly when fileStatus is "complete".
UploadedFile = Annotated[
    Annotated[CompletedFile, pydantic.Tag(_COMPLETE_TAG)]
    | Annotated[UnfinishedFile, pydantic.Tag(_UNFINISHED_TAG)],
    pydantic.Discriminator(_file_tag),
]
_UPLOADED_FILE = pydantic.TypeAdapter(UploadedFile)


class UploadCallbackPayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    upload_status: str = pydantic.Field(validation_alias="uploadStatus")
    form: dict[str, Any] = pydantic.Field(default_factory=dict)
    _files: list[CompletedFile | UnfinishedFile] = pydantic.PrivateAttr(
        default_factory=list
    )

    @pydantic.model_validator(mode="after")
    def _parse_files(self) -> UploadCallbackPayload:
        """Validate every file entry up front, so a bad one is a 422."""
        for value in self.form.values():
            entries = value if isinstance(value, list) else [value]

            self._files.extend(
                _UPLOADED_FILE.validate_python(entry)
                for entry in entries
                if isinstance(entry, dict) and "fileId" in entry
            )

        return self

    def uploaded_documents(self) -> list[models.UploadedDocument]:
        for file in self._files:
            if isinstance(file, UnfinishedFile):
                logger.info(
                    "Skipping file %s with status %s (error code %s)",
                    file.file_id,
                    file.file_status,
                    file.error_code,
                )

        return [
            models.UploadedDocument(file_id=file.file_id, s3_key=file.s3_key)
            for file in self._files
            if isinstance(file, CompletedFile)
        ]


class StagedDocumentResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        alias_generator=pydantic.alias_generators.to_camel, populate_by_name=True
    )

    file_id: ids.FileId
    parsing_status: models.ParsingStatus
    parsing_error: str | None = None
    title: str | None = None
    version: str | None = None
    last_modified: dt.datetime | None = None
    # The document this file was converted into, and when it was committed: set
    # once the conversion finishes, so a caller that gave up waiting on POST
    # /guides can tell whether it finished anyway.
    document_id: ids.DocumentId | None = None
    promoted_at: dt.datetime | None = None
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
