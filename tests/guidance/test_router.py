"""Creating a document from an upload, over HTTP.

The database and the staging store are stand-ins holding dictionaries, and the
conversion is stubbed: what these cases are about is the endpoint's own decisions -
which status a caller gets, what is recorded in which collection and in what order,
and what happens when the same journey is submitted twice or an attempt stopped part
way.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

import pymongo.errors
import pytest
from fastapi.testclient import TestClient

from app.common import mongo
from app.entrypoints.fastapi import app
from app.guidance import models, records, router, service
from app.guidance.documents.staging import models as staging_models
from app.guidance.documents.staging import router as staging_router
from app.guidance.parsing.errors import DocumentParseError

UPLOAD = "854a1f43-aab4-4579-b166-d708c0aad436"
FILE = "bf9b3179-47d0-4104-9944-5e23421ef437"
KEY = f"{UPLOAD}/{FILE}"

DOCUMENT = uuid.UUID("0199b8a2-4c1e-7b3a-9d2f-6a1e3c5b7d90")
VERSION = uuid.UUID("0199b8a2-4c1f-7e21-8b4c-2f9a6d1e3b57")
CONTENT = f"s3://rpa-ai-guidance-hub-docs/{DOCUMENT}/{VERSION}/content.md"

# What the stored document calls itself, which is not what the author typed on the
# form: one is the cover of the .docx, the other is METADATA["title"].
COVER_TITLE = "CS Revenue Claims Processing to Final Payment Guide"

METADATA = {
    "guidanceType": "process",
    "title": "CS Revenue Claims",
    "intendedAudience": "Case workers",
    "owners": ["Revenue Claims Team"],
}

# Who was signed in, which is not who owns the document - see `records`.
AUTHOR = {"id": "dev-user-123", "displayName": "Dev User"}


def _request(**overrides: Any) -> dict[str, Any]:
    body = {
        "source": {
            "uploadId": UPLOAD,
            "fileId": FILE,
            "filename": "CS Revenue Claims.docx",
        },
        "metadata": METADATA,
        "createdBy": AUTHOR,
    }
    body.update(overrides)
    return body


def _at(document: dict[str, Any], path: str) -> Any:
    """A value by its dotted path, as Mongo addresses a nested field."""
    value: Any = document
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _ordered(documents: list[dict[str, Any]], order: Any) -> list[dict[str, Any]]:
    """`documents` in the order Mongo's `sort` argument asks for.

    Applied last key first, which is how a stable sort composes into the ordering a
    multi-key sort means.
    """
    ordered = list(documents)
    for field, direction in reversed(list(order)):
        ordered.sort(key=lambda document: document[field], reverse=direction < 0)
    return ordered


class FakeCursor:
    """What `find` answers: an ordering, then an iteration."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def sort(self, order: Any) -> FakeCursor:
        self.documents = _ordered(self.documents, order)
        return self

    async def __aiter__(self) -> Any:
        for document in self.documents:
            yield document


class FakeCollection:
    """Just enough collection to hold records and find them again."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def find_one(
        self, query: dict[str, Any], sort: Any = None
    ) -> dict[str, Any] | None:
        found = self._matching(query)
        if sort is not None:
            found = _ordered(found, sort)
        return found[0] if found else None

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor(self._matching(query))

    async def insert_one(self, document: dict[str, Any]) -> None:
        """Refuses a second record under one `_id`, as Mongo does."""
        if any(stored["_id"] == document["_id"] for stored in self.documents):
            message = f"duplicate key: {document['_id']}"
            raise pymongo.errors.DuplicateKeyError(message)
        self.documents.append(document)

    def _matching(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            document
            for document in self.documents
            if all(_at(document, path) == value for path, value in query.items())
        ]


class FakeDatabase(dict):
    """A database of the two collections this endpoint writes to."""


@pytest.fixture
def collections():
    """Both collections, with the app pointed at them."""
    stored = {
        records.DOCUMENTS: FakeCollection(),
        records.VERSIONS: FakeCollection(),
    }
    database = FakeDatabase(stored)

    async def _database() -> FakeDatabase:
        return database

    app.dependency_overrides[mongo.get_db] = _database
    yield stored
    app.dependency_overrides.clear()


class FakeStaging:
    """Staged files by id. Reserving gives every file the same pair of ids, the ones
    the stubbed conversion answers with."""

    def __init__(self) -> None:
        self.staged: dict[str, staging_models.StagedDocument] = {}

    def stage(
        self,
        file_id: str = FILE,
        path: str = KEY,
        status: staging_models.ParsingStatus = staging_models.ParsingStatus.COMPLETE,
    ) -> None:
        now = dt.datetime.now(tz=dt.UTC)
        self.staged[file_id] = staging_models.StagedDocument(
            file_id=file_id,
            parsing_status=status,
            path=path,
            created_at=now,
            updated_at=now,
        )

    async def get(self, file_id: str) -> staging_models.StagedDocument | None:
        return self.staged.get(file_id)

    async def reserve_ids(self, file_id: str) -> staging_models.StagedDocument | None:
        staged = self.staged.get(file_id)
        if staged is None or staged.parsing_status != "complete":
            return None
        if staged.document_id is None:
            staged = dataclasses.replace(
                staged, document_id=DOCUMENT, version_id=VERSION
            )
            self.staged[file_id] = staged
        return staged

    async def promote(self, file_id: str) -> None:
        staged = self.staged.get(file_id)
        if staged is not None:
            self.staged[file_id] = dataclasses.replace(
                staged, promoted_at=dt.datetime.now(tz=dt.UTC)
            )


@pytest.fixture
def staging(collections):  # noqa: ARG001 - its overrides are cleared with these
    """A staging store holding the journey's file, parsed."""
    fake = FakeStaging()
    fake.stage()
    app.dependency_overrides[staging_router.get_staging_store] = lambda: fake
    return fake


@pytest.fixture
def documents(collections):
    return collections[records.DOCUMENTS]


@pytest.fixture
def versions(collections):
    return collections[records.VERSIONS]


@pytest.fixture
def converts(mocker):
    """The conversion, answering where a version went without doing any of it."""
    return mocker.patch.object(
        router.service,
        "convert",
        return_value=models.StoredDocument(
            document_id=DOCUMENT,
            version_id=VERSION,
            content=CONTENT,
            title=COVER_TITLE,
            sections=23,
            images=45,
        ),
    )


@pytest.fixture
def client(collections, converts, staging):  # noqa: ARG001 - wanted for their effect
    """A client that does not start the app's lifespan.

    As the health and main tests build one, and for the same reason: the lifespan
    connects to MongoDB, and these cases are about the endpoint rather than about
    the service coming up.
    """
    return TestClient(app)


class TestCreatingADocument:
    def test_a_converted_document_is_answered_with_the_version_it_made(self, client):
        response = client.post("/guides", json=_request())

        assert response.status_code == 201
        assert response.json()["id"] == str(DOCUMENT)
        assert [version["id"] for version in response.json()["versions"]] == [
            str(VERSION)
        ]
        assert response.json()["versions"][0]["contentUrl"] == CONTENT

    def test_where_the_document_can_be_read_from_afterwards(self, client):
        response = client.post("/guides", json=_request())

        assert response.headers["Location"] == f"/guides/{DOCUMENT}"

    def test_what_the_author_said_is_recorded_against_the_document(
        self, client, documents
    ):
        """Metadata is the document's, not the version's: re-converting must not
        disturb what an author said about what the document is."""
        client.post("/guides", json=_request())

        record = documents.documents[0]
        assert record["_id"] == DOCUMENT
        assert record["metadata"] == METADATA
        assert record["source"]["uploadId"] == UPLOAD
        assert "createdAt" in record

    def test_where_the_content_went_is_recorded_against_the_version(
        self, client, versions
    ):
        client.post("/guides", json=_request())

        record = versions.documents[0]
        assert record["_id"] == VERSION
        assert record[records.DOCUMENT_ID] == DOCUMENT
        assert record["contentUrl"] == CONTENT
        assert "createdAt" in record
        assert "source" not in record

    def test_what_the_document_called_itself_is_kept_beside_the_version(
        self, client, versions
    ):
        """A copy of the stored document's own title, so a list of versions reads as
        something a person can follow. Reading a version back takes the title from the
        content; this is what it was called *then*, which nothing else records."""
        client.post("/guides", json=_request())

        assert versions.documents[0]["title"] == COVER_TITLE
        assert versions.documents[0]["title"] != METADATA["title"]

    def test_the_ids_are_the_ones_the_object_store_used(
        self, client, documents, versions
    ):
        """A record and a key are two spellings of one address. The document's id is
        the prefix it was written under and the version's id is the prefix beneath
        that, so a content URL can be read straight off the pair."""
        client.post("/guides", json=_request())

        document_id = documents.documents[0]["_id"]
        version_id = versions.documents[0]["_id"]

        assert versions.documents[0][records.DOCUMENT_ID] == document_id
        assert versions.documents[0]["contentUrl"] == (
            f"s3://rpa-ai-guidance-hub-docs/{document_id}/{version_id}/content.md"
        )

    def test_who_was_signed_in_is_recorded_against_the_version(self, client, versions):
        """The version's creator, which the document's owners are not: one is who
        pressed the button, the other is who is answerable for the document."""
        client.post("/guides", json=_request())

        assert versions.documents[0]["createdBy"] == AUTHOR

    def test_a_version_made_by_nobody_is_recorded_as_nobody(self, client, versions):
        """The endpoint is not authenticated yet. A version attributed to a guess
        would be worse than one attributed to no one."""
        client.post("/guides", json=_request(createdBy=None))

        assert versions.documents[0]["createdBy"] is None


class TestSubmittingTheSameJourneyTwice:
    """A refresh, a back button, a retried request. Each of those means the same
    version, and converting a second time would spend the work to make another."""

    def test_the_second_submission_answers_the_first_document(self, client):
        first = client.post("/guides", json=_request())
        second = client.post("/guides", json=_request())

        assert second.status_code == 200
        assert second.json() == first.json()

    def test_and_does_not_convert_again(self, client, converts):
        client.post("/guides", json=_request())
        client.post("/guides", json=_request())

        assert converts.call_count == 1

    def test_and_does_not_record_a_second_document_or_version(
        self, client, documents, versions
    ):
        client.post("/guides", json=_request())
        client.post("/guides", json=_request())

        assert len(documents.documents) == 1
        assert len(versions.documents) == 1


class TestWhenItCannotBeDone:
    def test_a_source_this_will_not_read_is_a_bad_request(self, client, converts):
        converts.side_effect = service.SourceRefusedError("not the bucket")

        response = client.post("/guides", json=_request())

        assert response.status_code == 400

    def test_a_source_that_is_not_there_is_a_not_found(self, client, converts):
        converts.side_effect = service.SourceMissingError("nothing there")

        response = client.post("/guides", json=_request())

        assert response.status_code == 404

    def test_something_that_is_not_a_word_document_is_unprocessable(
        self, client, converts
    ):
        converts.side_effect = DocumentParseError("not a .docx")

        response = client.post("/guides", json=_request())

        assert response.status_code == 422

    def test_nothing_is_recorded_when_the_conversion_fails(
        self, client, converts, documents, versions
    ):
        """Neither half: both records are written only after the content is stored,
        so a conversion that fails must leave no record of either."""
        converts.side_effect = DocumentParseError("not a .docx")

        client.post("/guides", json=_request())

        assert documents.documents == []
        assert versions.documents == []


class TestTheUploadIsTheStagedOne:
    """The file is named by its id, and read from its staging record: never from a
    location the caller supplies."""

    def test_the_file_is_converted_from_where_it_was_staged(self, client, converts):
        client.post("/guides", json=_request())

        source_url = converts.call_args.args[0]
        assert source_url.endswith(f"/{KEY}")
        assert source_url.startswith("s3://")

    def test_a_file_that_was_never_staged_is_not_found(self, client, staging):
        staging.staged.clear()

        response = client.post("/guides", json=_request())

        assert response.status_code == 404

    def test_a_file_from_another_upload_is_a_bad_request(self, client, staging):
        staging.stage(path=f"another-upload/{FILE}")

        response = client.post("/guides", json=_request())

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "status",
        [
            staging_models.ParsingStatus.PENDING,
            staging_models.ParsingStatus.IN_PROGRESS,
            staging_models.ParsingStatus.FAILED,
        ],
    )
    def test_a_file_that_has_not_parsed_is_a_conflict(
        self, client, staging, converts, status
    ):
        staging.stage(status=status)

        response = client.post("/guides", json=_request())

        assert response.status_code == 409
        converts.assert_not_called()


class TestTheIdsAreReservedOnce:
    """The document and version ids come from the staging record, so every attempt
    at one upload writes under the same ones."""

    def test_the_content_is_stored_under_the_reserved_ids(self, client, converts):
        client.post("/guides", json=_request())

        assert converts.call_args.kwargs["document_id"] == DOCUMENT
        assert converts.call_args.kwargs["version_id"] == VERSION

    def test_the_records_are_written_bottom_up(self, client, mocker):
        """The version once its content is stored, then the document, which commits
        it."""
        order = mocker.Mock()
        order.attach_mock(mocker.spy(records, "create_version"), "version")
        order.attach_mock(mocker.spy(records, "create"), "document")

        client.post("/guides", json=_request())

        assert [name for name, *_ in order.mock_calls] == ["version", "document"]

    def test_an_attempt_that_stopped_after_its_version_is_finished(
        self, client, documents, versions
    ):
        """The earlier attempt stored the content and recorded the version, then
        failed before the document. Submitting again commits the document, and keeps
        the version it found rather than recording a second."""
        earlier = {
            "_id": VERSION,
            records.DOCUMENT_ID: DOCUMENT,
            "contentUrl": CONTENT,
            "title": COVER_TITLE,
            "createdBy": None,
            "createdAt": dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.UTC),
        }
        versions.documents.append(earlier)

        response = client.post("/guides", json=_request())

        assert response.status_code == 201
        assert [record["_id"] for record in documents.documents] == [DOCUMENT]
        assert versions.documents == [earlier]

    def test_a_version_id_taken_by_another_document_is_raised(
        self, client, versions, documents
    ):
        """No attempt at this document could have recorded that, so it is a fault
        rather than an earlier attempt's work - and nothing is committed."""
        versions.documents.append(
            {
                "_id": VERSION,
                records.DOCUMENT_ID: "another",
                "contentUrl": "s3://rpa-ai-guidance-hub-docs/another/content.md",
                "title": None,
                "createdBy": None,
                "createdAt": dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.UTC),
            }
        )
        request = _request()

        with pytest.raises(records.MisfiledVersionError):
            client.post("/guides", json=request)

        assert documents.documents == []


class TestTheStagedFileIsPromoted:
    """Once the document is committed the staged file has been promoted to it, and
    its staging record says so - so a record with ids but no promotion is an attempt
    that never committed."""

    def test_once_the_document_is_committed(self, client, staging):
        response = client.post("/guides", json=_request())

        assert response.status_code == 201
        assert staging.staged[FILE].promoted_at is not None

    def test_failing_to_mark_it_does_not_fail_the_request(
        self, client, staging, documents, mocker, caplog
    ):
        """The document is already safe, and is the upload's record from here on."""
        mocker.patch.object(staging, "promote", side_effect=RuntimeError("Mongo away"))

        response = client.post("/guides", json=_request())

        assert response.status_code == 201
        assert [record["_id"] for record in documents.documents] == [DOCUMENT]
        assert "as promoted" in caplog.text

    def test_not_when_the_conversion_fails(self, client, staging, converts):
        """Nothing was committed: the record keeps its ids for the next attempt, and
        is not promoted."""
        converts.side_effect = DocumentParseError("not a .docx")

        client.post("/guides", json=_request())

        assert staging.staged[FILE].document_id == DOCUMENT
        assert staging.staged[FILE].promoted_at is None


class TestReadingADocumentBack:
    def test_a_document_that_was_created(self, client):
        client.post("/guides", json=_request())

        response = client.get(f"/guides/{DOCUMENT}")

        assert response.status_code == 200
        assert response.json()["metadata"] == METADATA
        assert [version["id"] for version in response.json()["versions"]] == [
            str(VERSION)
        ]

    def test_a_document_that_never_was(self, client):
        assert client.get(f"/guides/{uuid.uuid4()}").status_code == 404

    def test_an_id_that_is_not_a_uuid_is_refused_before_anything_is_looked_up(
        self, client, mocker
    ):
        """No document could have such an id, so there is nothing to look for."""
        find = mocker.spy(records, "find")

        response = client.get("/guides/no-such-document")

        assert response.status_code == 422
        find.assert_not_called()

    def test_its_content_is_the_newest_versions_as_it_is_stored(self, client, mocker):
        """As stored rather than re-rendered: what the file says about its pictures
        resolves against the URL it was read from, so a caller keeping that URL needs
        nothing else."""
        client.post("/guides", json=_request())
        mocker.patch.object(router.store, "read", return_value=b"# CS Revenue Claims")

        response = client.get(f"/guides/{DOCUMENT}/content")

        assert response.status_code == 200
        assert response.text == "# CS Revenue Claims"

    def test_the_content_of_a_document_that_never_was(self, client):
        assert client.get(f"/guides/{uuid.uuid4()}/content").status_code == 404

    def test_the_content_of_an_id_that_is_not_a_uuid_is_refused(self, client, mocker):
        latest = mocker.spy(records, "latest_version")

        response = client.get("/guides/no-such-document/content")

        assert response.status_code == 422
        latest.assert_not_called()

    def test_a_version_recorded_but_whose_content_has_gone(self, client, mocker):
        """The record points at something that is not there. Not a 404, which would
        say the document does not exist when it does."""
        client.post("/guides", json=_request())
        mocker.patch.object(router.store, "read", return_value=None)

        response = client.get(f"/guides/{DOCUMENT}/content")

        assert response.status_code == 502


class TestGuidanceDependencies:
    def test_get_guidance_service_wiring(self, mocker):
        mock_s3 = mocker.MagicMock()
        guidance_svc = router.get_guidance_service(s3_client=mock_s3)
        assert isinstance(guidance_svc, service.GuidanceService)
        assert guidance_svc._s3 is mock_s3
