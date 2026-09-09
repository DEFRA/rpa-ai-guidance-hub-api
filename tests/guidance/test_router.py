"""Creating a document from an upload, over HTTP.

The database is a stand-in holding dictionaries, and the conversion is stubbed: what
these cases are about is the endpoint's own decisions - which status a caller gets,
what is recorded in which collection, and what happens when the same journey is
submitted twice.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.common import mongo
from app.entrypoints.fastapi import app
from app.guidance import records, router, service
from app.guidance.parsing.errors import DocumentParseError

UPLOAD = "854a1f43-aab4-4579-b166-d708c0aad436"
FILE = "bf9b3179-47d0-4104-9944-5e23421ef437"
KEY = f"{UPLOAD}/{FILE}"

DOCUMENT = "01JBQ8"
VERSION = "01JBQ9"
CONTENT = f"s3://rpa-ai-guidance-hub-docs/{DOCUMENT}/{VERSION}/content.md"
ASSETS = f"s3://rpa-ai-guidance-hub-docs/{DOCUMENT}/assets/"

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
            "url": f"s3://rpa-ai-guidance-hub-source-docs/{KEY}",
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
        return_value=service.StoredDocument(
            document_id=DOCUMENT,
            version_id=VERSION,
            content=CONTENT,
            assets=ASSETS,
            title="CS Revenue Claims",
            sections=23,
            images=45,
        ),
    )


@pytest.fixture
def client(collections, converts):  # noqa: ARG001 - both are wanted for their effect
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
        assert response.json()["id"] == DOCUMENT
        assert [version["id"] for version in response.json()["versions"]] == [VERSION]
        assert response.json()["versions"][0]["content"] == CONTENT
        assert response.json()["versions"][0]["assets"] == ASSETS

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
        assert "createdAt" in record

    def test_where_the_content_went_is_recorded_against_the_version(
        self, client, versions
    ):
        client.post("/guides", json=_request())

        record = versions.documents[0]
        assert record["_id"] == VERSION
        assert record[records.DOCUMENT_ID] == DOCUMENT
        assert record["source"]["uploadId"] == UPLOAD
        assert record["content"]["url"] == CONTENT
        assert "createdAt" in record

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
        """Neither half: the document is written first, so a conversion that fails
        before it must leave no record of either."""
        converts.side_effect = DocumentParseError("not a .docx")

        client.post("/guides", json=_request())

        assert documents.documents == []
        assert versions.documents == []


class TestReadingADocumentBack:
    def test_a_document_that_was_created(self, client):
        client.post("/guides", json=_request())

        response = client.get(f"/guides/{DOCUMENT}")

        assert response.status_code == 200
        assert response.json()["metadata"] == METADATA
        assert [version["id"] for version in response.json()["versions"]] == [VERSION]

    def test_a_document_that_never_was(self, client):
        assert client.get("/guides/no-such-document").status_code == 404

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
        assert client.get("/guides/no-such-document/content").status_code == 404

    def test_a_version_recorded_but_whose_content_has_gone(self, client, mocker):
        """The record points at something that is not there. Not a 404, which would
        say the document does not exist when it does."""
        client.post("/guides", json=_request())
        mocker.patch.object(router.store, "read", return_value=None)

        response = client.get(f"/guides/{DOCUMENT}/content")

        assert response.status_code == 502
