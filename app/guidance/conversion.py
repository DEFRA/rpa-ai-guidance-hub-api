"""Converting an upload and saving the document it becomes, off the API's queue.

A document with many pictures takes a while to save, one write per picture, so the
request that starts it does not wait: it sends a `ConversionJob` to the queue and
answers at once, and the caller follows the save on the file's staging record. The
consumer gives each message to `ConversionHandler`, which does the work.

**Once the document is committed, search is given the version to index**, and only
then is the save marked complete, so the message stays until search has it. Search
answers before it has indexed it: the hub and search agree in the end, not at once.

**A job names the document its version is of:** a new one, whose record, written after
the version, commits it; or one there already, a guide's later version, which its own
record commits. A later version is written with its sections' ids: those of the same
sections in the version before, so what points into a section follows it.

**Safe to repeat**, as SQS may deliver a message more than once: the ids are reserved
once, on the staging record, every write creates rather than replaces and keeps what
is already there, a version already recorded is not converted again, and search keeps
only the newest version it is given.

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

import dataclasses
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any, Protocol

import anyio.from_thread
import fastapi.concurrency
import pydantic

from app.common import tracing
from app.guidance import ids, models, records, schemas, section_ids, service
from app.guidance.parsing import errors
from app.search import search_service

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pymongo.asynchronous.database

    from app.background import sqs
    from app.guidance.documents.staging import store as staging_store

logger = logging.getLogger(__name__)

# What converting an upload, or giving its version to search, raises that retrying
# cannot fix.
_UNFIXABLE = (
    service.SourceRefusedError,
    service.SourceMissingError,
    errors.DocumentParseError,
    records.MisfiledVersionError,
    search_service.IndexRefusedError,
)


class SearchIndex(Protocol):
    """Where each saved version goes, to be indexed as its guide's latest."""

    async def index(
        self, version: models.Version, metadata: Mapping[str, Any]
    ) -> None: ...


class ConversionJob(pydantic.BaseModel):
    """One upload to convert and save: everything the work needs, so nothing has to be
    asked of the request that started it, which has been answered by the time it runs."""

    file_id: ids.FileId
    source_url: str
    document_id: ids.DocumentId
    version_id: ids.VersionId
    request: schemas.NewDocument
    # The request's trace id, so that the work, and what it asks of search, is traced
    # with it. None for a request that had none.
    trace_id: str | None = None


class ConversionSubmitter(Protocol):
    """Takes a job to be run, and returns before it has run."""

    async def submit(self, job: ConversionJob) -> None: ...


class QueueSubmitter:
    """Sends each job to the queue, as the JSON `ConversionHandler` reads back."""

    def __init__(self, queue: sqs.SqsQueue) -> None:
        self._queue = queue

    async def submit(self, job: ConversionJob) -> None:
        await self._queue.send(job.model_dump_json(by_alias=True))


class ConversionHandler:
    """Runs one job, for the consumer: see the module docstring."""

    def __init__(
        self,
        database: pymongo.asynchronous.database.AsyncDatabase,
        staging: staging_store.StagingStore,
        guidance: service.GuidanceService,
        search: SearchIndex,
        *,
        s3_client: Any,
    ) -> None:
        self._database = database
        self._staging = staging
        self._guidance = guidance
        self._search = search
        # Where the version before's content is read from, to carry its sections' ids.
        self._s3 = s3_client

    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        job = ConversionJob.model_validate_json(body)
        # Reset after, as the consumer's loop works other jobs in this context.
        traced = tracing.ctx_trace_id.set(job.trace_id or "")
        try:
            await self._work(job, last_delivery=last_delivery)
        finally:
            tracing.ctx_trace_id.reset(traced)

    async def _work(self, job: ConversionJob, *, last_delivery: bool) -> None:
        try:
            document = await self._save(job)
            await self._give_to_search(job, document)
            await self._staging.promote(job.file_id)
        except _UNFIXABLE as unfixable:
            logger.warning("Could not save file %s: %s", job.file_id, unfixable)
            await self._staging.fail_saving(job.file_id, str(unfixable))
        except Exception as failure:
            if last_delivery:
                await self._staging.fail_saving(job.file_id, str(failure))
            raise

    async def _save(self, job: ConversionJob) -> models.Document:
        """Convert the upload and record its version, unless an earlier delivery did,
        then its document, unless that is there already: the document, as it is
        recorded.

        Bottom up. The version is recorded only once its content is wholly stored, and
        the document last: until it exists nothing can reach the version, and once it
        does everything beneath it is there.
        """
        version = await records.find_version(
            self._database, job.document_id, job.version_id
        )
        if version is None:
            version = await self._converted(job)
        return await records.create(
            self._database,
            job.request.document_for(job.document_id, version.created_at),
        )

    async def _converted(self, job: ConversionJob) -> models.Version:
        """Convert the upload, and record the version it makes, with its sections' ids
        where there is a version before to carry them from.

        Converting is done in a worker thread, because parsing and the object store
        calls block.
        """
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

        made = job.request.version_for(stored, dt.datetime.now(tz=dt.UTC))
        given = await section_ids.of_new_version(
            self._database, job.document_id, stored.content, self._s3
        )
        return await records.create_version(
            self._database, dataclasses.replace(made, section_ids=given)
        )

    async def _give_to_search(
        self, job: ConversionJob, document: models.Document
    ) -> None:
        """Give search the version as it was recorded, with the document's details as
        they are answered: what the hub keeps, not what a later request asked. Read
        back, so that a delivery after one that saved the version but could not give it
        to search gives it all the same."""
        [version] = [
            version
            for version in await records.versions_of(self._database, document.id)
            if version.id == job.version_id
        ]
        await self._search.index(version, models.details_as_now(document.metadata))

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
