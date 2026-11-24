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
from app.common import tracing
from app.guidance import conversion, models, records, schemas, section_ids, service
from app.guidance.documents import store
from app.guidance.documents.staging import store as staging_store
from app.guidance.parsing import errors
from app.guidance.parsing import models as parsing_models
from app.search import search_service
from tests.fakes import search_index as fake_search_index
from tests.fixtures import word_documents

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
def search() -> fake_search_index.InMemorySearchIndex:
    return fake_search_index.InMemorySearchIndex()


@pytest.fixture
def handler(database, staging, search) -> conversion.ConversionHandler:
    return conversion.ConversionHandler(
        database,
        staging,
        service.GuidanceService(s3_client=None),
        search,
        s3_client=None,
    )


@pytest.fixture
async def saved(handler, body, converts):  # noqa: ARG001 - for its effect
    """The document, made by the first job."""
    await handler(body, last_delivery=False)


@pytest.fixture
async def next_job(
    saved,  # noqa: ARG001 - the first job has to have run with its own conversion
    staging,
    job,
    converts,
) -> conversion.ConversionJob:
    """A second upload's job, naming the first one's document."""
    next_job = await _new_version_job(staging, job, "2.0")
    converts.return_value = _stored(next_job)
    return next_job


@pytest.fixture
def contents(mocker, job):
    """What each version stored, as the hub writes it: every one after the first
    renumbers the first's sections, and puts a new one before them."""
    first = f"s3://docs/{job.document_id}/{job.version_id}/content.md"
    return mocker.patch.object(
        section_ids.store,
        "read",
        side_effect=lambda url, s3_client=None: (  # noqa: ARG005
            _FIRST_CONTENT if url == first else _LATER_CONTENT
        ).encode(),
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

    @pytest.mark.usefixtures("converts")
    async def test_gives_search_the_saved_version_with_its_details(
        self, handler, body, job, search
    ):
        await handler(body, last_delivery=False)

        [(version, metadata)] = search.indexed
        assert (version.id, version.title) == (job.version_id, "Claims guide")
        assert metadata["guideTitle"] == "Claims"

    async def test_gives_search_the_details_saved_not_a_later_requests(
        self, handler, body, job, database, search, converts
    ):
        """An earlier delivery saved the document; this job's request may hold other
        answers, a later submission's, but search is given what the hub keeps."""
        saved = job.request.model_copy(update={"metadata": {"guideTitle": "As saved"}})
        await records.create_version(
            database, saved.version_for(converts.return_value, _NOW)
        )
        await records.create(database, saved.document_for(job.document_id, _NOW))

        await handler(body, last_delivery=False)

        [(_, metadata)] = search.indexed
        assert metadata["guideTitle"] == "As saved"

    @pytest.mark.usefixtures("converts")
    async def test_gives_search_the_version_under_the_requests_trace_id(
        self, handler, job, search, mocker
    ):
        traced: list[str | None] = []
        indexing = search.index

        async def index(version, metadata):
            traced.append(tracing.ctx_trace_id.get(None))
            await indexing(version, metadata)

        mocker.patch.object(search, "index", side_effect=index)
        before = tracing.ctx_trace_id.get(None)
        body = job.model_copy(update={"trace_id": "trace-123"}).model_dump_json(
            by_alias=True
        )

        await handler(body, last_delivery=False)

        assert traced == ["trace-123"]
        assert tracing.ctx_trace_id.get(None) == before

    async def test_delivered_again_gives_search_the_version_it_could_not_give(
        self, handler, body, job, staging, search, converts
    ):
        """Search was away when the version was saved: the next delivery gives it the
        version, without converting the upload again, and only then marks the save."""
        search.failure = search_service.SearchServiceError("search away")
        with pytest.raises(search_service.SearchServiceError):
            await handler(body, last_delivery=False)
        search.failure = None

        await handler(body, last_delivery=False)

        assert [version.id for version, _ in search.indexed] == [job.version_id]
        converts.assert_called_once()
        assert (await staging.get(job.file_id)).saving_status == "complete"


_FIRST_CONTENT = (
    "# Claims guide\n\n"
    "## 1 Apply for the payment\n\nSend the form.\n\n"
    "## 2 Get paid\n\nWe pay you.\n"
)
_LATER_CONTENT = (
    "# Claims guide\n\n"
    "## 1 Check you can apply\n\nRead the rules.\n\n"
    "## 2 Apply for the payment\n\nSend the form.\n\n"
    "## 3 Get paid\n\nWe pay you.\n"
)


async def _new_version_job(
    staging,
    job: conversion.ConversionJob,
    version_number: str,
    upload: str | None = None,
) -> conversion.ConversionJob:
    """Another upload's job, naming `job`'s document: a new version of its guide, that
    replaces `job`'s version."""
    upload, file_id = upload or str(uuid.uuid4()), str(uuid.uuid4())
    await staging.claim(file_id, f"{upload}/{file_id}")
    await staging.mark_complete(
        file_id, parsing_models.MinimalDocumentInfo(title="Claims guide")
    )
    reserved = await staging.reserve_ids(file_id, job.document_id)
    await staging.start_saving(file_id)
    source = schemas.Source.model_validate({"uploadId": upload, "fileId": file_id})
    return job.model_copy(
        update={
            "file_id": file_id,
            "source_url": f"s3://rpa-ai-guidance-hub-source-docs/{upload}/{file_id}",
            "version_id": reserved.version_id,
            "follows": job.version_id,
            "request": job.request.model_copy(
                update={"source": source, "version_number": version_number}
            ),
        }
    )


# Details given with a later upload, unlike the first one's.
_CHANGED_DETAILS = {"schemes": ["Scheme B"], "owner": "new.owner@example.com"}


def _with_details(
    job: conversion.ConversionJob, details: dict[str, object]
) -> conversion.ConversionJob:
    """`job`, with the details given with its upload."""
    return job.model_copy(
        update={"request": job.request.model_copy(update={"metadata": details})}
    )


def _stored(job: conversion.ConversionJob) -> models.StoredDocument:
    """Where `job`'s conversion stored its version."""
    return models.StoredDocument(
        document_id=job.document_id,
        version_id=job.version_id,
        content=f"s3://docs/{job.document_id}/{job.version_id}/content.md",
        title="Claims guide",
        sections=3,
        images=2,
    )


class TestConvertingAnUploadIntoANewVersion:
    """A job naming a document that is there already: a new version of a guide, which
    its own record commits."""

    async def test_fails_when_the_content_of_the_version_it_follows_is_not_there(
        self, handler, job, next_job, staging, database, mocker
    ):
        """Its sections could not keep their ids, and no later delivery could give them
        back, as a recorded version is not converted again: so it is not recorded."""
        first = f"s3://docs/{job.document_id}/{job.version_id}/content.md"
        mocker.patch.object(
            section_ids.store,
            "read",
            side_effect=lambda url, s3_client=None: (  # noqa: ARG005
                None if url == first else _LATER_CONTENT.encode()
            ),
        )

        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        assert (await staging.get(next_job.file_id)).saving_status == "failed"
        versions = await records.versions_of(database, job.document_id)
        assert [version.id for version in versions] == [job.version_id]

    @pytest.mark.usefixtures("contents")
    async def test_keeps_the_details_given_with_it_on_its_own_record(
        self, handler, next_job, database
    ):
        changed = _with_details(next_job, _CHANGED_DETAILS)

        await handler(changed.model_dump_json(by_alias=True), last_delivery=False)

        stored = await database[records.VERSIONS].find_one({"_id": changed.version_id})
        assert stored["metadata"] == _CHANGED_DETAILS

    async def test_is_left_to_be_delivered_again_when_what_it_replaces_is_not_there(
        self, handler, next_job
    ):
        unknown = next_job.model_copy(update={"follows": uuid.uuid4()})
        body = unknown.model_dump_json(by_alias=True)

        with pytest.raises(LookupError):
            await handler(body, last_delivery=False)

    @pytest.mark.usefixtures("contents")
    async def test_is_newer_than_the_version_it_follows_whatever_the_time(
        self, handler, job, next_job, database
    ):
        """A version saved between this one's conversion and its save can hold a later
        time than now; the version that follows it must still be the newest, or every
        later version of the guide would be matched with the one before it."""
        later = dt.datetime.now(tz=dt.UTC) + dt.timedelta(hours=1)
        await database[records.VERSIONS].update_one(
            {"_id": job.version_id}, {"$set": {"createdAt": later}}
        )

        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        latest = await records.latest_version(database, job.document_id)
        assert latest.id == next_job.version_id

    @pytest.mark.usefixtures("contents")
    async def test_of_two_that_replace_one_version_the_second_saved_fails(
        self, handler, job, next_job, staging, converts, database
    ):
        """Both were asked for while the first version was the latest: the guide has
        changed since the second one's maintainer chose what to replace."""
        third_job = await _new_version_job(staging, job, "3.0")
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)
        converts.return_value = _stored(third_job)

        await handler(third_job.model_dump_json(by_alias=True), last_delivery=False)

        assert (await staging.get(third_job.file_id)).saving_status == "failed"
        versions = await records.versions_of(database, job.document_id)
        assert [version.id for version in versions] == [
            next_job.version_id,
            job.version_id,
        ]

    @pytest.mark.usefixtures("contents")
    async def test_two_files_of_one_upload_make_one_version(
        self, handler, job, next_job, staging, converts, database
    ):
        """A caller sent another file of the upload while the first file's save was
        under way: both replace the same version."""
        upload = next_job.request.source.upload_id
        other_job = await _new_version_job(staging, job, "2.0", upload=upload)
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)
        converts.return_value = _stored(other_job)

        await handler(other_job.model_dump_json(by_alias=True), last_delivery=False)

        assert (await staging.get(other_job.file_id)).saving_status == "failed"
        versions = await records.versions_of(database, job.document_id)
        assert [version.id for version in versions] == [
            next_job.version_id,
            job.version_id,
        ]

    @pytest.mark.usefixtures("contents")
    async def test_its_sections_keep_the_ids_they_had_before(
        self, handler, job, next_job, database
    ):
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        before = await section_ids.of_version(database, job.version_id, [])
        after = await section_ids.of_version(database, next_job.version_id, [])
        assert after["2-apply-for-the-payment"] == before["1-apply-for-the-payment"]
        assert after["3-get-paid"] == before["2-get-paid"]

    @pytest.mark.usefixtures("contents")
    async def test_a_section_new_to_it_has_a_new_id(
        self, handler, job, next_job, database
    ):
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        before = await section_ids.of_version(database, job.version_id, [])
        after = await section_ids.of_version(database, next_job.version_id, [])
        assert after["1-check-you-can-apply"] not in before.values()

    @pytest.mark.usefixtures("contents")
    async def test_keeps_each_sections_heading_beside_its_id(
        self, handler, next_job, database
    ):
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        after = await section_ids.of_version(database, next_job.version_id, [])
        headings = await section_ids.headings_of(database, [next_job.version_id])
        assert headings[next_job.version_id][after["3-get-paid"]] == "3 Get paid"

    @pytest.mark.usefixtures("contents")
    async def test_saves_it_as_the_documents_newest_version(
        self, handler, job, next_job, database
    ):
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        versions = await records.versions_of(database, job.document_id)
        assert [version.id for version in versions] == [
            next_job.version_id,
            job.version_id,
        ]

    @pytest.mark.usefixtures("contents")
    async def test_keeps_the_documents_record_as_it_was(
        self, handler, job, next_job, database
    ):
        await handler(next_job.model_dump_json(by_alias=True), last_delivery=False)

        document = await records.find(database, job.document_id)
        assert document.upload_id == job.request.source.upload_id

    @pytest.mark.usefixtures("contents")
    async def test_delivered_again_converts_it_once(self, handler, next_job, converts):
        body = next_job.model_dump_json(by_alias=True)

        await handler(body, last_delivery=False)
        await handler(body, last_delivery=False)

        assert converts.call_count == 2  # the document's first version, and this one


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

    async def test_includes_a_word_document_that_is_not_well_formed(
        self, handler, body, job, staging, mocker
    ):
        """Read for real, so python-docx's own error is what reaches the handler."""
        mocker.patch.object(
            store, "read", return_value=word_documents.with_malformed_xml()
        )

        await handler(body, last_delivery=False)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "failed"
        assert "not well-formed XML" in staged.save_error

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

    @pytest.mark.usefixtures("converts")
    async def test_includes_search_refusing_the_version(
        self, handler, body, job, staging, search
    ):
        search.failure = search_service.IndexRefusedError("422 no guide title")

        await handler(body, last_delivery=False)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "failed"
        assert staged.save_error == "422 no guide title"


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

    @pytest.mark.usefixtures("converts")
    async def test_includes_search_failing_so_the_save_is_not_marked(
        self, handler, body, job, staging, search
    ):
        search.failure = search_service.SearchServiceError("search away")

        with pytest.raises(search_service.SearchServiceError):
            await handler(body, last_delivery=False)

        staged = await staging.get(job.file_id)
        assert staged.saving_status == "in_progress"
        assert staged.promoted_at is None

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
        queue = sqs.SqsQueue(client, url, polling_threads=1)
        working = consumer.Consumer(queue, handler, concurrency=1, wait_seconds=1)
        working.start()

        await conversion.QueueSubmitter(queue).submit(job)
        async with asyncio.timeout(15):
            while await records.find(database, job.document_id) is None:
                await asyncio.sleep(0.1)
        await working.stop()

        assert await records.find(database, job.document_id) is not None
