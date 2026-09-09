"""Reaching a guide in a bucket.

boto3 is stubbed rather than run, so these keep the unit suite's property of needing
nothing running. What they are for is the two things only this scheme has to get
right: turning a URL into a bucket and a key, and telling "no such object" from
"something is wrong" - the second of which can be wrong quietly.

Everything else about storing a guide is the same whatever answers the URL, and is
tested once against `file://` in `test_store.py`.

All fixture text is invented, as everywhere in this package.
"""

import io

import pytest
from botocore.exceptions import ClientError

from app.guidance.documents import store
from app.guidance.parsing import models

GUIDE = "s3://docs/01JBQ8/01JBQ9"

# Pictures beside the document, said the way a document says it. A relative
# prefix has to resolve to a key rather than to a path with a dot in it.
BESIDE = "./assets"


def _error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetObject")


class FakeS3:
    """Whatever the call should do, and a record of what it was asked."""

    def __init__(self, *, body=None, error=None):
        self.body = body
        self.error = error
        self.gets: list[dict] = []
        self.puts: list[dict] = []

    def get_object(self, **kwargs):
        self.gets.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"Body": io.BytesIO(self.body if self.body is not None else b"")}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {}


@pytest.fixture
def s3(mocker):
    """Replace the client the store builds, leaving its own logic in place."""

    def install(*, body=None, error=None):
        fake = FakeS3(body=body, error=error)
        mocker.patch.object(store, "_s3", return_value=fake)
        return fake

    return install


class TestAddressing:
    def test_the_bucket_is_the_host_and_the_key_is_the_path(self, s3):
        fake = s3(body=b"# Claims")

        store.read("s3://docs/01JBQ8/01JBQ9/content.md")

        assert fake.gets[0] == {
            "Bucket": "docs",
            "Key": "01JBQ8/01JBQ9/content.md",
        }

    def test_what_was_escaped_to_survive_a_url_is_not_part_of_the_key(self, s3):
        """A document named after the file it came from is escaped into the URL.
        The object's key is the name, not the escaping."""
        fake = s3(body=b"")

        store.read("s3://docs/CS%20Revenue%202026/content.md")

        assert fake.gets[0]["Key"] == "CS Revenue 2026/content.md"

    def test_writing_puts_the_bytes_at_that_bucket_and_key(self, s3):
        fake = s3()

        store.save(models.MarkdownDocument(title="Claims"), GUIDE, BESIDE)

        assert fake.puts[0]["Bucket"] == "docs"
        assert fake.puts[0]["Key"] == "01JBQ8/01JBQ9/content.md"
        assert b"# Claims" in fake.puts[0]["Body"]


class TestWhatIsNotThere:
    def test_a_key_that_is_not_there_is_not_an_error(self, s3):
        """ "No such guide" is an ordinary answer, and making a caller read an error
        code to reach it is how it gets read wrongly."""
        s3(error=_error("NoSuchKey"))

        assert store.load(GUIDE) is None

    def test_a_bucket_that_is_not_there_is_raised(self, s3):
        """Swallowed as "not found", a mistyped bucket name would make every guide
        in the service report that it does not exist - which looks exactly like an
        empty store and says nothing about the cause."""
        s3(error=_error("NoSuchBucket"))

        with pytest.raises(ClientError):
            store.load(GUIDE)

    def test_being_refused_is_raised(self, s3):
        s3(error=_error("AccessDenied"))

        with pytest.raises(ClientError):
            store.read(f"{GUIDE}/content.md")


class TestStoringAGuideInABucket:
    def test_the_pictures_go_where_the_document_says_they_are(self, s3):
        """The addresses written into the document and the keys the pictures are
        written to are one answer, not two that have to agree."""
        assets = "s3://other-docs/01JBQ8"
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])
        fake = s3()

        store.save(document, GUIDE, assets)

        picture = next(put for put in fake.puts if put["Bucket"] == "other-docs")
        stored = next(put for put in fake.puts if put["Bucket"] == "docs")
        assert picture["Key"] == "01JBQ8/a3f9.png"
        assert f"![]({assets}/a3f9.png)".encode() in stored["Body"]

    def test_a_picture_is_stored_as_the_kind_of_picture_it_is(self, s3):
        """A stored document names its pictures by URL, and something else fetches
        them. Answered as `application/octet-stream`, a PNG is one a browser will
        not draw."""
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        fake = s3()

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]), GUIDE, BESIDE
        )

        picture = next(put for put in fake.puts if put["Key"].endswith("a3f9.png"))
        markdown = next(put for put in fake.puts if put["Key"].endswith("content.md"))
        assert picture["ContentType"] == "image/png"
        assert markdown["ContentType"] == "text/markdown; charset=utf-8"

    def test_the_pictures_are_written_before_the_document_naming_them(self, s3):
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        fake = s3()

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]), GUIDE, BESIDE
        )

        assert fake.puts[0]["Key"].endswith("a3f9.png")
        assert fake.puts[-1]["Key"].endswith("content.md")


class TestAVersionSharingTheDocumentsPictures:
    """How a document with versions is laid out, and the one thing that makes it
    work: the address a version writes for its pictures resolves above itself."""

    DOCUMENT = "s3://docs/b6ba5c0f"
    VERSION = f"{DOCUMENT}/9d4e1a77"
    UP = "../assets"

    def test_the_pictures_land_under_the_document_not_under_the_version(self, s3):
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])
        fake = s3()

        store.save(document, self.VERSION, self.UP)

        picture = next(put for put in fake.puts if put["Key"].endswith("a3f9.png"))
        markdown = next(put for put in fake.puts if put["Key"].endswith("content.md"))
        assert picture["Key"] == "b6ba5c0f/assets/a3f9.png"
        assert markdown["Key"] == "b6ba5c0f/9d4e1a77/content.md"

    def test_the_stored_file_says_the_relative_address_not_the_resolved_one(self, s3):
        """What is written into the file has to be the relative form, because that is
        what resolves to the same pictures from a version that does not exist yet."""
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        fake = s3()

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]),
            self.VERSION,
            self.UP,
        )

        stored = next(put for put in fake.puts if put["Key"].endswith("content.md"))
        assert b"![](../assets/a3f9.png)" in stored["Body"]

    def test_two_versions_write_their_pictures_to_one_key(self, s3):
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])
        fake = s3()

        store.save(document, self.VERSION, self.UP)
        store.save(document, f"{self.DOCUMENT}/e177bb03", self.UP)

        pictures = {put["Key"] for put in fake.puts if put["Key"].endswith("a3f9.png")}
        assert pictures == {"b6ba5c0f/assets/a3f9.png"}

    def test_a_relative_address_becomes_a_key_something_can_actually_fetch(self, s3):
        """S3 has no notion of a relative key: handed `../assets/a3f9.png` it would
        look for an object with `..` in its name. This is the resolution that stands
        between a stored document and reading a picture out of it."""
        fake = s3(body=b"\x89PNG")

        store.read(store.resolved(f"{self.VERSION}/content.md", "../assets/a3f9.png"))

        assert fake.gets[0] == {"Bucket": "docs", "Key": "b6ba5c0f/assets/a3f9.png"}
