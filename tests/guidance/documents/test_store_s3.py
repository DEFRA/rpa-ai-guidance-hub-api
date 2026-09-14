"""Reaching a guide in a bucket.

boto3 is mocked with moto, so these keep the unit suite's property of needing
nothing running. What they are for is the two things only this scheme has to get
right: turning a URL into a bucket and a key, and telling "no such object" from
"something is wrong" - the second of which can be wrong quietly.

Everything else about storing a guide is the same whatever answers the URL, and is
tested once against `file://` in `test_store.py`.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.guidance.documents import store
from app.guidance.parsing import models

GUIDE = "s3://docs/01JBQ8/01JBQ9"

# Pictures beside the document, said the way a document says it. A relative
# prefix has to resolve to a key rather than to a path with a dot in it.
BESIDE = "./assets"


@pytest.fixture
def s3(moto_s3: Any, mocker: Any) -> Any:
    """A moto S3 client with the default 'docs' bucket pre-created,
    wired into the store's S3 client factory."""
    moto_s3.create_bucket(
        Bucket="docs",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    mocker.patch.object(store, "_s3", return_value=moto_s3)
    return moto_s3


class TestAddressing:
    def test_the_bucket_is_the_host_and_the_key_is_the_path(self, s3: Any) -> None:
        s3.put_object(
            Bucket="docs",
            Key="01JBQ8/01JBQ9/content.md",
            Body=b"# Claims",
        )

        assert store.read("s3://docs/01JBQ8/01JBQ9/content.md") == b"# Claims"

    def test_what_was_escaped_to_survive_a_url_is_not_part_of_the_key(
        self, s3: Any
    ) -> None:
        """A document named after the file it came from is escaped into the URL.
        The object's key is the name, not the escaping."""
        s3.put_object(
            Bucket="docs",
            Key="CS Revenue 2026/content.md",
            Body=b"# Claims",
        )

        assert store.read("s3://docs/CS%20Revenue%202026/content.md") == b"# Claims"

    def test_writing_puts_the_bytes_at_that_bucket_and_key(self, s3: Any) -> None:
        store.save(models.MarkdownDocument(title="Claims"), GUIDE, BESIDE)

        stored = s3.get_object(Bucket="docs", Key="01JBQ8/01JBQ9/content.md")
        assert b"# Claims" in stored["Body"].read()


class TestWhatIsNotThere:
    @pytest.mark.usefixtures("s3")
    def test_a_key_that_is_not_there_is_not_an_error(self) -> None:
        """ "No such guide" is an ordinary answer, and making a caller read an error
        code to reach it is how it gets read wrongly."""
        assert store.load(GUIDE) is None

    def test_a_bucket_that_is_not_there_is_raised(self, s3: Any) -> None:
        """Swallowed as "not found", a mistyped bucket name would make every guide
        in the service report that it does not exist - which looks exactly like an
        empty store and says nothing about the cause."""
        s3.delete_bucket(Bucket="docs")

        with pytest.raises(ClientError):
            store.load(GUIDE)

    def test_being_refused_is_raised(self, s3: Any) -> None:
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": "arn:aws:s3:::docs/*",
                }
            ],
        }
        s3.put_bucket_policy(Bucket="docs", Policy=json.dumps(policy))

        with pytest.raises(ClientError):
            store.read(f"{GUIDE}/content.md")


class TestStoringAGuideInABucket:
    def test_the_pictures_go_where_the_document_says_they_are(self, s3: Any) -> None:
        """The addresses written into the document and the keys the pictures are
        written to are one answer, not two that have to agree."""
        s3.create_bucket(
            Bucket="other-docs",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        assets = "s3://other-docs/01JBQ8"
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])

        store.save(document, GUIDE, assets)

        picture = s3.get_object(Bucket="other-docs", Key="01JBQ8/a3f9.png")
        stored = s3.get_object(Bucket="docs", Key="01JBQ8/01JBQ9/content.md")
        assert picture["Body"].read() == b"\x89PNG"
        assert f"![]({assets}/a3f9.png)".encode() in stored["Body"].read()

    def test_a_picture_is_stored_as_the_kind_of_picture_it_is(self, s3: Any) -> None:
        """A stored document names its pictures by URL, and something else fetches
        them. Answered as `application/octet-stream`, a PNG is one a browser will
        not draw."""
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]), GUIDE, BESIDE
        )

        picture = s3.get_object(Bucket="docs", Key="01JBQ8/01JBQ9/assets/a3f9.png")
        markdown = s3.get_object(Bucket="docs", Key="01JBQ8/01JBQ9/content.md")
        assert picture["ContentType"] == "image/png"
        assert markdown["ContentType"] == "text/markdown; charset=utf-8"

    def test_the_pictures_are_written_before_the_document_naming_them(
        self, s3: Any, mocker: Any
    ) -> None:
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        spy = mocker.spy(s3, "put_object")

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]), GUIDE, BESIDE
        )

        assert spy.call_args_list[0].kwargs["Key"].endswith("a3f9.png")
        assert spy.call_args_list[-1].kwargs["Key"].endswith("content.md")


class TestAVersionSharingTheDocumentsPictures:
    """How a document with versions is laid out, and the one thing that makes it
    work: the address a version writes for its pictures resolves above itself."""

    DOCUMENT = "s3://docs/b6ba5c0f"
    VERSION = f"{DOCUMENT}/9d4e1a77"
    UP = "../assets"

    def test_the_pictures_land_under_the_document_not_under_the_version(
        self, s3: Any
    ) -> None:
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])

        store.save(document, self.VERSION, self.UP)

        picture = s3.get_object(Bucket="docs", Key="b6ba5c0f/assets/a3f9.png")
        markdown = s3.get_object(Bucket="docs", Key="b6ba5c0f/9d4e1a77/content.md")
        assert picture["Body"].read() == b"\x89PNG"
        assert markdown["Body"].read()

    def test_the_stored_file_says_the_relative_address_not_the_resolved_one(
        self, s3: Any
    ) -> None:
        """What is written into the file has to be the relative form, because that is
        what resolves to the same pictures from a version that does not exist yet."""
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )

        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]),
            self.VERSION,
            self.UP,
        )

        stored = s3.get_object(Bucket="docs", Key="b6ba5c0f/9d4e1a77/content.md")
        assert b"![](../assets/a3f9.png)" in stored["Body"].read()

    def test_two_versions_write_their_pictures_to_one_key(self, s3: Any) -> None:
        image = models.Image(name="a3f9.png", content_type="image/png", data=b"\x89PNG")
        section = models.MarkdownSection(
            heading="Evidence", ordinal=1, content="![](a3f9.png)", images=[image]
        )
        document = models.MarkdownDocument(title="Claims", sections=[section])

        store.save(document, self.VERSION, self.UP)
        store.save(document, f"{self.DOCUMENT}/e177bb03", self.UP)

        resp = s3.list_objects_v2(Bucket="docs", Prefix="b6ba5c0f/assets/")
        pictures = {obj["Key"] for obj in resp.get("Contents", [])}
        assert pictures == {"b6ba5c0f/assets/a3f9.png"}

    def test_a_relative_address_becomes_a_key_something_can_actually_fetch(
        self, s3: Any
    ) -> None:
        """S3 has no notion of a relative key: handed `../assets/a3f9.png` it would
        look for an object with `..` in its name. This is the resolution that stands
        between a stored document and reading a picture out of it."""
        s3.put_object(
            Bucket="docs",
            Key="b6ba5c0f/assets/a3f9.png",
            Body=b"\x89PNG",
        )

        content = store.read(
            store.resolved(f"{self.VERSION}/content.md", "../assets/a3f9.png")
        )

        assert content == b"\x89PNG"
