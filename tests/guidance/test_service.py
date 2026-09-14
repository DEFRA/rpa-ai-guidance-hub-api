"""Converting an uploaded document into a stored document.

The object store is stubbed at its own boundary - `store.read` and `store.save` -
rather than at boto3, because what these cases are about is which locations a
document
and its pictures are written to, and that is this module's decision. How a location
is reached is tested in `documents/test_store_s3.py`.

All fixture text is invented, as everywhere in this package.
"""

import pytest

from app.guidance import service
from app.guidance.documents import store
from app.guidance.parsing import models

UPLOAD = "854a1f43-aab4-4579-b166-d708c0aad436"
FILE = "bf9b3179-47d0-4104-9944-5e23421ef437"
KEY = f"{UPLOAD}/{FILE}"
SOURCE_BUCKET = "rpa-ai-guidance-hub-source-docs"
SOURCE = f"s3://{SOURCE_BUCKET}/{KEY}"


@pytest.fixture
def stored(mocker):
    """The store, answering with a document and recording what it was asked."""
    calls = {}

    def save(document, document_url_prefix, assets_url_prefix):
        calls["document"] = document
        calls["into"] = document_url_prefix
        calls["assets"] = assets_url_prefix
        return store.content_url(document_url_prefix)

    mocker.patch.object(store, "read", return_value=b"a docx, as far as this knows")
    mocker.patch.object(store, "save", save)
    mocker.patch.object(
        service.parser,
        "parse_docx",
        return_value=models.MarkdownDocument(
            title="Claims",
            sections=[models.MarkdownSection(heading="Applying", ordinal=1)],
        ),
    )
    return calls


class TestWhereAConvertedDocumentGoes:
    def test_the_markdown_goes_under_the_document_and_then_its_version(self, stored):
        converted = service.convert(SOURCE, document_id="01JBQ8", version_id="01JBQ9")

        assert stored["into"] == "s3://rpa-ai-guidance-hub-docs/01JBQ8/01JBQ9"
        assert converted.content == f"{stored['into']}/content.md"

    def test_the_document_is_told_to_call_its_pictures_relatively(self, stored):
        """A version says `../assets`, which resolves one step up - to the document.
        Absolute would name this version, and the pictures are not this version's."""
        service.convert(SOURCE, document_id="01JBQ8", version_id="01JBQ9")

        assert stored["assets"] == "../assets"

    def test_and_that_resolves_to_the_documents_own_pictures(self, stored):
        """What the store is told to write to, resolved: one step above the version,
        so it is the document's pictures and not this version's."""
        service.convert(SOURCE, document_id="01JBQ8", version_id="01JBQ9")

        assert store.assets_url(stored["into"], stored["assets"]) == (
            "s3://rpa-ai-guidance-hub-docs/01JBQ8/assets/"
        )

    def test_two_versions_of_one_document_share_its_pictures(self, stored):
        """The whole reason the address is relative. A picture is named by the digest
        of its own bytes, so a second conversion rewrites the Markdown and writes not
        one image again."""
        first = service.convert(SOURCE, document_id="01JBQ8", version_id="01JBQ9")
        where_first_put_them = store.assets_url(stored["into"], stored["assets"])

        second = service.convert(SOURCE, document_id="01JBQ8", version_id="01JBQA")
        where_second_put_them = store.assets_url(stored["into"], stored["assets"])

        assert first.content != second.content
        assert where_first_put_them == where_second_put_them

    @pytest.mark.usefixtures("stored")
    def test_a_document_given_no_id_is_given_one(self):
        first = service.convert(SOURCE)
        second = service.convert(SOURCE)

        assert first.document_id != second.document_id

    @pytest.mark.usefixtures("stored")
    def test_a_version_given_no_id_is_given_one(self):
        first = service.convert(SOURCE, document_id="01JBQ8")
        second = service.convert(SOURCE, document_id="01JBQ8")

        assert first.version_id != second.version_id

    @pytest.mark.usefixtures("stored")
    def test_what_was_converted_is_reported(self):
        converted = service.convert(SOURCE, document_id="01JBQ8")

        assert converted.title == "Claims"
        assert converted.sections == 1
        assert converted.images == 0


class TestWhatItWillNotRead:
    """The bucket and key come from a caller and the store reads what it is given,
    so this is the whole of what stops the endpoint reading any object the service
    can reach."""

    @pytest.mark.usefixtures("stored")
    def test_a_bucket_that_is_not_the_upload_bucket(self):
        with pytest.raises(service.SourceRefusedError, match="not the bucket"):
            service.convert(f"s3://somebody-elses-bucket/{KEY}")

    @pytest.mark.usefixtures("stored")
    def test_a_key_that_is_not_the_shape_cdp_uploader_writes(self):
        with pytest.raises(service.SourceRefusedError, match="not the shape"):
            service.convert(f"s3://{SOURCE_BUCKET}/secrets/credentials.json")

    @pytest.mark.usefixtures("stored")
    def test_a_key_that_escapes_its_prefix(self):
        with pytest.raises(service.SourceRefusedError):
            service.convert(f"s3://{SOURCE_BUCKET}/../{KEY}")

    @pytest.mark.usefixtures("stored")
    def test_a_location_that_is_not_in_an_object_store_at_all(self):
        """A `file://` URL would read this container's own disk, which is why the
        scheme is checked and not only the bucket."""
        with pytest.raises(service.SourceRefusedError, match="not an s3"):
            service.convert("file:///etc/passwd")

    def test_nothing_is_read_before_the_location_is_accepted(self, mocker):
        """Refused first, and read second: a check made after the read has already
        happened is not a check."""
        read = mocker.patch.object(store, "read")

        with pytest.raises(service.SourceRefusedError):
            service.convert(f"s3://somebody-elses-bucket/{KEY}")

        read.assert_not_called()


class TestWhenTheDocumentIsNotThere:
    def test_a_source_that_is_missing_is_told_apart_from_one_that_is_bad(self, mocker):
        mocker.patch.object(store, "read", return_value=None)

        with pytest.raises(service.SourceMissingError, match="No document at"):
            service.convert(SOURCE)
