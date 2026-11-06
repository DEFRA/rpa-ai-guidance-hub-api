"""Converting an upload and saving the document it becomes, off the API's queue.

A document with many pictures takes a while to save, one write per picture, so the
request that starts it does not wait: it sends a `ConversionJob` to the queue and
answers at once, and the caller follows the save on the file's staging record. The
consumer gives each message to `ConversionHandler`, which does the work.

**Safe to repeat**, as SQS may deliver a message more than once: the ids are reserved
once, on the staging record, every write creates rather than replaces and keeps what
is already there, and a document already committed is only marked as saved.

**What becomes of a failure** decides what becomes of the message:

- one retrying cannot fix (`_UNFIXABLE`) is recorded on the staging record and returned
  from, so the message is deleted;
- any other is raised, so SQS delivers the message again; on its last delivery it is
  recorded first, so nobody is left following a save that will never finish, and SQS
  then moves the message to the dead-letter queue;
- a message that is not a job names no upload to record anything on, so it is raised
  and ends on the dead-letter queue.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Protocol

import anyio.from_thread
import fastapi.concurrency
import pydantic

from app.guidance import ids, records, schemas, service
from app.guidance.parsing import errors

if TYPE_CHECKING:
    from collections.abc import Callable

    import pymongo.asynchronous.database

    from app.background import sqs
    from app.guidance.documents.staging import store as staging_store

logger = logging.getLogger(__name__)

# What converting an upload raises that retrying cannot fix.
_UNFIXABLE = (
    service.SourceRefusedError,
    service.SourceMissingError,
    errors.DocumentParseError,
    records.MisfiledVersionError,
)


class ConversionJob(pydantic.BaseModel):
    """One upload to convert and save: everything the work needs, so nothing has to be
    asked of the request that started it, which has been answered by the time it runs."""

    file_id: ids.FileId
    source_url: str
    document_id: ids.DocumentId
    version_id: ids.VersionId
    request: schemas.NewDocument


class ConversionSubmitter(Protocol):
    """Takes a job to be run, and returns before it has run."""

    async def submit(self, job: ConversionJob) -> None: ...


class QueueSubmitter:
    """Sends each job to the queue, as the JSON `ConversionHandler` reads back."""

    def __init__(self, queue: sqs.SqsQueue) -> None:
        self._queue = queue

    async def submit(self, job: ConversionJob) -> None:
        await self._queue.send(job.model_dump_json(by_alias=True))


class NoQueueError(RuntimeError):
    """Raised for a job there is no queue to send to."""


class NoQueue:
    """Where no queue is configured: nothing would run a job, so it is refused."""

    async def submit(self, _job: ConversionJob) -> None:
        message = "No queue is configured to convert uploads"
        raise NoQueueError(message)


class ConversionHandler:
    """Runs one job, for the consumer: see the module docstring."""

    def __init__(
        self,
        database: pymongo.asynchronous.database.AsyncDatabase,
        staging: staging_store.StagingStore,
        guidance: service.GuidanceService,
    ) -> None:
        self._database = database
        self._staging = staging
        self._guidance = guidance

    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        job = ConversionJob.model_validate_json(body)
        try:
            await self._save(job)
            await self._staging.promote(job.file_id)
        except _UNFIXABLE as unfixable:
            logger.warning("Could not convert file %s: %s", job.file_id, unfixable)
            await self._staging.fail_saving(job.file_id, str(unfixable))
        except Exception as failure:
            if last_delivery:
                await self._staging.fail_saving(job.file_id, str(failure))
            raise

    async def _save(self, job: ConversionJob) -> None:
        """Convert the upload and record what it makes, unless an earlier delivery did.

        Converting is done in a worker thread, because parsing and the object store
        calls block.
        """
        upload_id = job.request.source.upload_id
        if await records.find_by_upload(self._database, upload_id) is not None:
            return

        stored = await fastapi.concurrency.run_in_threadpool(
            self._guidance.convert,
            job.source_url,
            document_id=job.document_id,
            version_id=job.version_id,
            on_progress=self._reporting_on(job.file_id),
        )
        logger.info(
            "Converted file %s into document %s version %s: %d sections, %d images",
            job.file_id,
            stored.document_id,
            stored.version_id,
            stored.sections,
            stored.images,
        )

        # Bottom up. The version is recorded only once its content is wholly stored,
        # and the document last, because writing it is what commits the document:
        # until it exists nothing can reach the version, and once it does everything
        # beneath it is there.
        now = dt.datetime.now(tz=dt.UTC)
        await records.create_version(
            self._database, job.request.version_for(stored, now)
        )
        await records.create(
            self._database, job.request.document_for(stored.document_id, now)
        )

    def _reporting_on(self, file_id: ids.FileId) -> Callable[[int, int], None]:
        """Tells the staging record each step of the save, from the worker thread.

        Best effort: progress is only for following the save, and no reason to lose it.
        """

        def on_progress(completed: int, total: int) -> None:
            try:
                anyio.from_thread.run(
                    self._staging.record_save_progress, file_id, completed, total
                )
            except Exception:
                logger.warning(
                    "Could not record step %d of %d of saving file %s",
                    completed,
                    total,
                    file_id,
                    exc_info=True,
                )

        return on_progress
