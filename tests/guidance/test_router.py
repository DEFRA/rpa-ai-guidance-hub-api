"""Creating a guide from an uploaded document, over HTTP.

The database is a stand-in holding dictionaries, and the conversion is stubbed: what
these cases are about is the endpoint's own decisions - which status a caller gets,
what is recorded, and what happens when the same journey is submitted twice.

All fixture text is invented, as everywhere in this package.
"""

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

METADATA = {
    "guidanceType": "process",
    "title": "CS Revenue Claims",
    "intendedAudience": "Case workers",
}


def _request(**overrides: Any) -> dict[str, Any]:
    body = {
        "source": {
            "uploadId": UPLOAD,
            "url": f"s3://rpa-ai-guidance-hub-source-docs/{KEY}",
            "filename": "CS Revenue Claims.docx",
        },
        "metadata": METADATA,
        "createdBy": "a-case-worker",
    }
    body.update(overrides)
    return body


class FakeGuides:
    """Just enough collection to hold guides and find them again."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self.documents:
            if all(_at(document, path) == value for path, value in query.items()):
                return document
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.documents.append(document)


def _at(document: dict[str, Any], path: str) -> Any:
    """A value by its dotted path, as Mongo addresses a nested field."""
    value: Any = document
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


class FakeDatabase(dict):
    """A database that only has guides in it."""


@pytest.fixture
def guides():
    """The collection the endpoint writes to, with the app pointed at it."""
    collection = FakeGuides()
    database = FakeDatabase({records.COLLECTION: collection})

    async def _database() -> FakeDatabase:
        return database

    app.dependency_overrides[mongo.get_db] = _database
    yield collection
    app.dependency_overrides.clear()


@pytest.fixture
def converts(mocker):
    """The conversion, answering where a guide went without doing any of it."""
    return mocker.patch.object(
        router.service,
        "convert",
        return_value=service.StoredDocument(
            document_id="01JBQ8",
            content="s3://managed-docs/guides/01JBQ8/content.md",
            assets="s3://managed-doc-assets/01JBQ8",
            title="CS Revenue Claims",
            sections=23,
            images=45,
        ),
    )


@pytest.fixture
def client(guides, converts):  # noqa: ARG001 - both are wanted for their effect
    """A client that does not start the app's lifespan.

    As the health and main tests build one, and for the same reason: the lifespan
    connects to MongoDB, and these cases are about the endpoint rather than about
    the service coming up.
    """
    return TestClient(app)


class TestCreatingAGuide:
    def test_a_converted_guide_is_answered_with_where_its_content_went(self, client):
        response = client.post("/guides", json=_request())

        assert response.status_code == 201
        assert response.json()["id"] == "01JBQ8"
        assert response.json()["content"] == (
            "s3://managed-docs/guides/01JBQ8/content.md"
        )
        assert response.json()["assets"] == "s3://managed-doc-assets/01JBQ8"

    def test_where_the_guide_can_be_read_from_afterwards(self, client):
        response = client.post("/guides", json=_request())

        assert response.headers["Location"] == "/guides/01JBQ8"

    def test_what_the_author_said_is_recorded_beside_where_the_content_is(
        self, client, guides
    ):
        client.post("/guides", json=_request())

        record = guides.documents[0]
        assert record["_id"] == "01JBQ8"
        assert record["metadata"] == METADATA
        assert record["source"]["uploadId"] == UPLOAD
        assert record["content"]["url"] == (
            "s3://managed-docs/guides/01JBQ8/content.md"
        )
        assert record["createdBy"] == "a-case-worker"
        assert "createdAt" in record


class TestSubmittingTheSameJourneyTwice:
    """A refresh, a back button, a retried request. Each of those means the same
    guide, and converting a second time would spend the work to make another."""

    def test_the_second_submission_answers_the_first_guide(self, client):
        first = client.post("/guides", json=_request())
        second = client.post("/guides", json=_request())

        assert second.status_code == 200
        assert second.json() == first.json()

    def test_and_does_not_convert_again(self, client, converts):
        client.post("/guides", json=_request())
        client.post("/guides", json=_request())

        assert converts.call_count == 1

    def test_and_does_not_record_a_second_guide(self, client, guides):
        client.post("/guides", json=_request())
        client.post("/guides", json=_request())

        assert len(guides.documents) == 1


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
        self, client, converts, guides
    ):
        converts.side_effect = DocumentParseError("not a .docx")

        client.post("/guides", json=_request())

        assert guides.documents == []


class TestReadingAGuideBack:
    def test_a_guide_that_was_created(self, client):
        client.post("/guides", json=_request())

        response = client.get("/guides/01JBQ8")

        assert response.status_code == 200
        assert response.json()["metadata"] == METADATA

    def test_a_guide_that_never_was(self, client):
        assert client.get("/guides/no-such-guide").status_code == 404

    def test_its_content_is_answered_as_it_is_stored(self, client, mocker):
        """As stored rather than re-rendered: the file addresses its pictures
        absolutely, so what a reader gets needs nothing else to make sense of it."""
        client.post("/guides", json=_request())
        mocker.patch.object(router.store, "read", return_value=b"# CS Revenue Claims")

        response = client.get("/guides/01JBQ8/content")

        assert response.status_code == 200
        assert response.text == "# CS Revenue Claims"

    def test_a_guide_recorded_but_whose_content_has_gone(self, client, mocker):
        """Not a 404: the guide exists, and saying it does not would send somebody
        looking for the wrong fault."""
        client.post("/guides", json=_request())
        mocker.patch.object(router.store, "read", return_value=None)

        assert client.get("/guides/01JBQ8/content").status_code == 502
