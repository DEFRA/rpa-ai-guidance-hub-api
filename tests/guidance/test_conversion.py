"""Converting an upload off the queue, against a real MongoDB (testcontainers). Reading
the Word document is stubbed: what these are about is what is recorded, what is safe to
repeat, and what becomes of the message when it fails.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid

import boto3
import pydantic
import pytest

from app.background import consumer, sqs
from app.guidance import conversion, models, records, schemas, service
from app.guidance.documents.staging import store as staging_store
from app.guidance.parsing import errors
from app.guidance.parsing import models as parsing_models

_NOW = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
async def database(mongo_database):
    await records.ensure_indexes(mongo_database)
    return mongo_database


@pytest.fixture
def staging(mongo_database) -> staging_store.MongoStagingStore:
    return staging_store.MongoStagingStore(mongo_database, retention_seconds=14400)


@pytest.fixture
async def job(staging) -> conversion.ConversionJob:
    """The job for a staged, parsed upload with its ids reserved, its save started."""
    upload, file_id = str(uuid.uuid4()), str(uuid.uuid4())
    await staging.claim(file_id, f"{upload}/{file_id}")
    await staging.mark_complete(
        file_id, parsing_models.MinimalDocumentInfo(title="Claims guide")
    )
    reserved = await staging.reserve_ids(file_id)
    await staging.start_saving(file_id)
    return conversion.ConversionJob(
        file_id=file_id,
        source_url=f"s3://rpa-ai-guidance-hub-source-docs/{upload}/{file_id}",
        document_id=reserved.document_id,
        version_id=reserved.version_id,
        request=schemas.NewDocument.model_validate(
            {
                "source": {"uploadId": upload, "fileId": file_id},
                "metadata": {"guideTitle": "Claims"},
                "versionNumber": "1.0",
                "createdBy": {"id": "dev-user-123", "displayName": "Dev User"},
            }
        ),
    )


@pytest.fixture
def body(job) -> str:
    return job.model_dump_json(by_alias=True)


@pytest.fixture
def converts(mocker, job):
    """Reading the Word document, answering where its version went."""
    return mocker.patch.object(
        service,
        "convert",
        return_value=models.StoredDocument(
            document_id=job.document_id,
            version_id=job.version_id,
            content=f"s3://docs/{job.document_id}/{job.version_id}/content.md",
            title="Claims guide",
            sections=3,
            images=2,
        ),
    )


@pytest.fixture
def handler(database, staging) -> conversion.ConversionHandler:
    return conversion.ConversionHandler(
        database, staging, service.GuidanceService(s3_client=None)
    )


class TestConvertingAnUpload:
    @pytest.mark.usefixtures("converts")
    async def test_saves_its_document_and_marks_the_save_complete(
        self, handler, body, job, database, staging
    ):
        await handler(body, last_delivery=False)

        assert await records.find(database, job.document_id) is not None
        staged = await staging.get(job.file_id)
        assert staged.saving_status == "complete"
        assert staged.promoted_at is not None

    async def test_records_each_step_of_the_save_as_it_happens(
        self, handler, body, job, staging, converts
    ):
        seen = []

        def convert(*_args, on_progress, **_kwargs):
            on_progress(1, 3)
            seen.append(
                asyncio.run_coroutine_threadsafe(staging.get(job.file_id), loop)
            )
            return converts.return_value

        loop = asyncio.get_running_loop()
        converts.side_effect = convert

        await handler(body, last_delivery=False)

        during = await asyncio.wrap_future(seen[0])
        assert (during.save_steps_completed, during.save_steps_total) == (1, 3)

    async def test_goes_on_when_a_step_cannot_be_recorded(
        self, handler, body, job, database, staging, converts, mocker, caplog
    ):
        """Progress is only for following the save: it is no reason to lose the work."""

        def convert(*_args, on_progress, **_kwargs):
            on_progress(1, 3)
            return converts.return_value

        converts.side_effect = convert
        mocker.patch.object(
            staging, "record_save_progress", side_effect=RuntimeError("Mongo away")
        )

        await handler(body, last_delivery=False)

        assert await records.find(database, job.document_id) is not None
        assert "Mongo away" in caplog.text

    async def test_delivered_again_saves_it_once(self, handler, body, converts):
        await handler(body, last_delivery=False)
        await handler(body, last_delivery=False)

        converts.assert_called_once()

    async def test_delivered_again_marks_a_save_that_could_not_be_marked(
        self, handler, body, job, staging, converts, mocker
    ):
        """The follower is waiting on that mark, so the message stays until it is made."""
        marking = mocker.patch.object(
            staging, "promote", side_effect=RuntimeError("Mongo away")
        )
        with pytest.raises(RuntimeError):
            await handler(body, last_delivery=False)
        marking.side_effect = None
        marking.reset_mock()

        await handler(body, last_delivery=False)

        marking.assert_awaited_once_with(job.file_id)
        converts.assert_called_once()


class TestAFailureRetryingCannotFix:
    """Recorded, and returned from, so the consumer deletes the message."""

    @pytest.mark.parametrize(
        "failure",
        [
            service.SourceRefusedError("not the bucket"),
            service.SourceMissingError("nothing there"),
            errors.DocumentParseError("not a .docx"),
        ],
    )
    async def test_is_recorded_with_its_reason(
        self, handler, body, job, staging, converts, failure
    ):
        converts.side_effect = failure

        await handler(body, last_delivery=False)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "failed"
        assert staged.save_error == str(failure)

    @pytest.mark.usefixtures("converts")
    async def test_includes_a_version_id_another_document_has(
        self, handler, body, job, database, staging
    ):
        await records.create_version(
            database,
            models.Version(
                id=job.version_id,
                document_id=uuid.uuid4(),
                content_url="s3://docs/other/content.md",
                title="Another guide",
                created_at=_NOW,
            ),
        )

        await handler(body, last_delivery=False)

        assert (await staging.get(job.file_id)).saving_status == "failed"
        assert await records.find(database, job.document_id) is None


class TestAnyOtherFailure:
    """Raised, so the consumer leaves the message for SQS to deliver again."""

    async def test_is_left_with_the_save_still_under_way(
        self, handler, body, job, staging, converts
    ):
        converts.side_effect = RuntimeError("database unavailable")

        with pytest.raises(RuntimeError):
            await handler(body, last_delivery=False)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "in_progress"
        assert staged.save_error is None

    async def test_on_its_last_delivery_is_recorded_too(
        self, handler, body, job, staging, converts
    ):
        """So nobody is left following a save that will never finish, as SQS then
        moves the message to the dead-letter queue."""
        converts.side_effect = RuntimeError("database unavailable")

        with pytest.raises(RuntimeError):
            await handler(body, last_delivery=True)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "failed"
        assert staged.save_error == "database unavailable"

    @pytest.mark.parametrize("not_a_job", ["not json", '{"fileId": "f-1"}'])
    async def test_includes_a_message_that_is_not_a_job(self, handler, not_a_job):
        """It names no upload to record a failure on, so the dead-letter queue is
        where it ends."""
        with pytest.raises(pydantic.ValidationError):
            await handler(not_a_job, last_delivery=True)


class TestSendingAJob:
    async def test_with_no_queue_configured_is_refused(self, job):
        submitter = conversion.NoQueue()

        with pytest.raises(conversion.NoQueueError):
            await submitter.submit(job)

    @pytest.mark.usefixtures("converts")
    async def test_through_the_queue_saves_its_document(
        self, floci_endpoint, handler, job, database
    ):
        """Over real HTTP, so the job has been through JSON and back, and the handler
        has run as a task of the consumer's rather than of a request."""
        client = boto3.client(
            "sqs",
            endpoint_url=floci_endpoint,
            region_name="eu-west-2",
            aws_access_key_id="test",
            aws_secret_access_key="test",  # noqa: S106 - floci takes any credentials
        )
        name = f"conversions-{uuid.uuid4().hex[:12]}"
        dead_letters = client.create_queue(QueueName=f"{name}-deadletter")["QueueUrl"]
        arn = client.get_queue_attributes(
            QueueUrl=dead_letters, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        url = client.create_queue(
            QueueName=name,
            Attributes={
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": arn, "maxReceiveCount": "3"}
                )
            },
        )["QueueUrl"]
        queue = sqs.SqsQueue(client, url)
        working = consumer.Consumer(queue, handler, concurrency=1, wait_seconds=1)
        working.start()

        await conversion.QueueSubmitter(queue).submit(job)
        async with asyncio.timeout(15):
            while await records.find(database, job.document_id) is None:
                await asyncio.sleep(0.1)
        await working.stop()

        assert await records.find(database, job.document_id) is not None
