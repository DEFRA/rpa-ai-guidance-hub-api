"""Converting an upload and saving the document it becomes, after the request that
asked for it has been answered.

A document with many pictures takes a while to save, one write per picture, so the
request that starts it does not wait: it hands a `ConversionJob` to a submitter and
answers at once, and the caller follows the save on the file's staging record.

The job is shaped as a message on a queue would be - it carries everything the work
needs - and `ConversionSubmitter` is the one place that decides how it is run. For now
`BackgroundTaskSubmitter` runs it as a FastAPI background task in this process, which
needs no infrastructure but does not survive the process: a save under way when the
service restarts is lost, and its staging record stays "in progress". A submitter that
puts the job on a queue replaces it without anything else changing.
"""

from __future__ import annotations

import datetime as dt
from logging import getLogger
from typing import TYPE_CHECKING, Protocol

import anyio.from_thread
from fastapi.concurrency import run_in_threadpool

from app.guidance import models, records, service

if TYPE_CHECKING:
    import fastapi
    import pymongo.asynchronous.database

    from app.guidance.documents.staging import store as staging_store

logger = getLogger(__name__)


class ConversionSubmitter(Protocol):
    """Takes a job to be run, and returns before it has run."""

    async def submit(self, job: models.ConversionJob) -> None: ...


class ConversionRunner:
    """Runs one job: converts the upload, records the version and then the document,
    and keeps the file's staging record up to date throughout."""

    def __init__(
        self,
        database: pymongo.asynchronous.database.AsyncDatabase,
        staging: staging_store.StagingStore,
        guidance: service.GuidanceService,
    ) -> None:
        self._database = database
        self._staging = staging
        self._guidance = guidance

    async def run(self, job: models.ConversionJob) -> None:
        """Run the job. A failure is recorded on the staging record, not raised:
        nothing is waiting on this but whoever follows that record.

        Converting is done in a worker thread, because parsing and the object store
        calls block. The thread reports each step of the save back on the event loop,
        which is where the staging record is written.
        """

        def on_progress(completed: int, total: int) -> None:
            anyio.from_thread.run(
                self._staging.record_save_progress, job.file_id, completed, total
            )

        try:
            stored = await run_in_threadpool(
                self._guidance.convert,
                job.source_url,
                document_id=job.document_id,
                version_id=job.version_id,
                on_progress=on_progress,
            )
            # Bottom up. The version is recorded only once its content is wholly
            # stored, and the document last, because writing it is what commits the
            # document: until it exists nothing can reach the version, and once it
            # does everything beneath it is there.
            now = dt.datetime.now(tz=dt.UTC)
            await records.create_version(self._database, job.version(stored, now))
            await records.create(self._database, job.document(now))
        except Exception as error:
            logger.exception(
                "Could not convert file %s into document %s",
                job.file_id,
                job.document_id,
            )
            await self._staging.fail_saving(job.file_id, str(error))
            return

        logger.info(
            "Converted file %s into document %s version %s: %d sections, %d images",
            job.file_id,
            stored.document_id,
            stored.version_id,
            stored.sections,
            stored.images,
        )
        await self._promote(job)

    async def _promote(self, job: models.ConversionJob) -> None:
        """Mark the file's staging record as promoted, now its document is committed.

        Best effort: the document is already safe, and is the upload's record from
        here on.
        """
        try:
            await self._staging.promote(job.file_id)
        except Exception:
            logger.warning(
                "Could not mark the staging record for file %s as promoted",
                job.file_id,
                exc_info=True,
            )


class BackgroundTaskSubmitter:
    """Runs each job as a FastAPI background task: after the reply, in this process."""

    def __init__(
        self, background_tasks: fastapi.BackgroundTasks, runner: ConversionRunner
    ) -> None:
        self._background_tasks = background_tasks
        self._runner = runner

    async def submit(self, job: models.ConversionJob) -> None:
        self._background_tasks.add_task(self._runner.run, job)
