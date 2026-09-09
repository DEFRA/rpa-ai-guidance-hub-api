"""Turning an uploaded .docx into a stored version of a document.

One operation, in the order the pieces become known: read the document cdp-uploader
delivered, parse it, and store the version it makes. What comes back is where that
version went, which is what the record in the database points at.

Two buckets, and they are different kinds of thing. The source bucket is somebody
else's layout - cdp-uploader owns those keys - and is only ever read. The docs bucket
is this service's, and holds a document laid out so that its versions share one set
of pictures:

    s3://<docs>/<document id>/assets/<digest>.<ext>
    s3://<docs>/<document id>/<version id>/content.md

The pictures sit above the versions because they belong to the document rather than
to any one version of it: a picture is named by the digest of its own bytes, so two
versions drawing the same picture name the same file, and converting a document again
rewrites the Markdown without duplicating a single image. What makes that work is the
`../assets` the stored file carries - a *relative* address, resolving to the same
place from every version - and `store.resolved` is what turns it back into an S3 key
for anything that has to fetch one.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid
from dataclasses import dataclass

from app import config
from app.guidance.documents import store
from app.guidance.parsing import parser

# What cdp-uploader names an object it has delivered: the upload it belonged to and
# the file within it, both uuids. Matched before anything is read, because a key is
# supplied by a caller and a store reads whatever key it is given.
_UPLOADER_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# What a stored version calls its pictures. Relative, and one step up, so that every
# version of a document resolves it to the same place: the pictures are the
# document's, not the version's.
_ASSETS = "../assets"


class SourceRefusedError(ValueError):
    """Raised for a source location this service will not read."""


class SourceMissingError(LookupError):
    """Raised when the source document is not where the caller said it was."""


@dataclass(frozen=True)
class StoredDocument:
    """Where a converted guide went, and what it turned out to be."""

    document_id: str
    version_id: str
    content: str
    assets: str
    title: str
    sections: int
    images: int


def convert(
    source_url: str,
    document_id: str | None = None,
    version_id: str | None = None,
) -> StoredDocument:
    """Convert the .docx at `source_url` into a stored version of a document.

    Takes the URL cdp-uploader's status reports rather than a bucket and a key,
    because that is the form it arrives in and taking it apart only to put it back
    together is two chances to do it differently.

    Raises:
        SourceRefusedError: if the caller named a location this will not read.
        SourceMissingError: if there is nothing there.
        DocumentParseError: if what is there is not a Word document.
    """
    settings = config.get_config()
    _refuse_anything_but_an_upload(source_url, settings.source_docs_s3_bucket)

    source = store.read(source_url)
    if source is None:
        message = f"No document at {source_url}"
        raise SourceMissingError(message)

    document = parser.parse_docx(source)

    document_id = document_id or str(uuid.uuid4())
    version_id = version_id or str(uuid.uuid4())

    document_url = store.document_url(f"s3://{settings.docs_s3_bucket}", document_id)
    into = store.document_url(document_url, version_id)

    return StoredDocument(
        document_id=document_id,
        version_id=version_id,
        content=store.save(document, into, _ASSETS),
        assets=store.assets_url(into, _ASSETS),
        title=document.title,
        sections=len(document.sections),
        images=len(document.images),
    )


def _refuse_anything_but_an_upload(source_url: str, expected: str) -> None:
    """Refuse a source location that is not one cdp-uploader wrote.

    The URL comes from a caller and the store reads whatever it is given, so without
    this the endpoint reads any object the service's credentials can reach. All three
    parts are checked: the scheme because a `file://` URL would read this container's
    own disk, the bucket because it says *whose* objects these are, and the shape of
    the key because a bucket alone would still allow anything that ever landed in it.
    """
    parsed = urllib.parse.urlsplit(source_url)

    if parsed.scheme != "s3":
        message = f"{source_url!r} is not an s3:// location"
        raise SourceRefusedError(message)

    if parsed.netloc != expected:
        message = (
            f"{parsed.netloc!r} is not the bucket uploaded documents are delivered to"
        )
        raise SourceRefusedError(message)

    if not _UPLOADER_KEY.match(parsed.path.lstrip("/")):
        message = f"{parsed.path!r} is not the shape of a key cdp-uploader writes"
        raise SourceRefusedError(message)
