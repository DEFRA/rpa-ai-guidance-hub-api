"""Where a converted guide is kept, and how it is got back.

A guide is a Markdown file and the pictures it draws, and saving one is told where
each of them goes:

    <document url prefix>/content.md
    <assets url prefix>/<digest>.<ext>

Those two prefixes are the whole of what a caller has to say. Their scheme says how
the guide is reached - `file://` for a directory, `s3://` for a bucket - and storing
is the same operation either way: the names, the order the writes go in and what the
document says about itself do not change with the medium, so none of them are
written down twice.

Nothing here decides where a guide lives. Keeping one as `<base>/<id>` with its
pictures in an `assets/` directory beside it is one arrangement, and `document_url` and
`assets_url` compose it, but it is the caller's arrangement rather than this module's:
pictures put in a bucket of their own are stored and read the same.

The Markdown is standalone. The assets prefix is what the stored document calls its
pictures, written into the file exactly as it was given, and its cross-references
point at anchors that headings in the same file print. Anything able to read Markdown
can read a stored guide with nothing else, and `reader.from_markdown` reads it back
into the model that wrote it, whichever form the pictures are named in.

**A relative assets prefix is an address in the document, not a place on a disk.**
`./assets` means "beside the document", and only the document's own URL says where
that is, so the pictures are written wherever the two prefixes together resolve to.
An absolute prefix names its place outright and is used as it stands. That is the
whole of the difference: what the document says is what the caller passed, either
way, and only the destination of the bytes is worked out.

Which to pass is a real choice with a cost either way. A document that named a
picture sitting right beside it `file:///home/...` would carry a machine's directory
layout in its text - configuration, written into a file that outlives it, and wrong
the moment the guide is copied anywhere. A document whose pictures live in a bucket
of their own has no neighbour to point at and has to say the whole address.
"""

from __future__ import annotations

import posixpath
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError

from app import config
from app.guidance.documents import reader
from app.guidance.parsing import models

if TYPE_CHECKING:
    from collections.abc import Callable

_CONTENT = "content.md"
_MARKDOWN = "text/markdown; charset=utf-8"

# The conventional place for a guide's pictures: a directory beside its Markdown. A
# trailing slash because it is concatenated with a bare name rather than joined.
ASSET_PREFIX = "assets/"

# What S3 says when an object is not there. A missing *bucket* is deliberately not
# among them: it means this service is pointed somewhere that does not exist, and
# answering "no such guide" to every question is how a mistyped bucket name looks
# exactly like an empty store.
_MISSING = ("NoSuchKey", "404")


class UnsupportedSchemeError(ValueError):
    """Raised for a location this cannot reach."""


def document_url(base: str, document_id: str) -> str:
    """The URL of the document `document_id` names beneath `base`.

    A convenience for callers that keep guides as `<base>/<id>`, and the one place
    an id is escaped: the dev tooling names a guide after the document it converted,
    and those hold spaces - a `#` in one would truncate every URL built from it.
    """
    return f"{_directory(base)}{_segment(document_id)}"


def content_url(document_url_prefix: str) -> str:
    """Where a guide's Markdown lives: one file per guide."""
    return f"{_directory(document_url_prefix)}{_CONTENT}"


def assets_url(document_url_prefix: str, assets_url_prefix: str) -> str:
    """Where a guide's pictures actually go.

    A relative assets prefix is an address in the document rather than a place: it
    says where the pictures are *from the document*, so the document's own URL is
    what turns it into somewhere to write. An absolute one already names its place
    and is answered unchanged.

    The path is normalised, so a prefix written the way a document would write it -
    `./assets`, or `../shared` for pictures a sibling guide also draws - names the
    same location as the plain form rather than a directory with a dot in its name.
    """
    if urllib.parse.urlsplit(assets_url_prefix).scheme:
        return _directory(assets_url_prefix)

    beneath = urllib.parse.urlsplit(
        f"{_directory(document_url_prefix)}{assets_url_prefix}"
    )
    resolved = beneath._replace(path=posixpath.normpath(beneath.path))

    return _directory(urllib.parse.urlunsplit(resolved))


def asset_url(document_url_prefix: str, assets_url_prefix: str, name: str) -> str:
    """Where one of a guide's pictures lives.

    Named for its content, so that converting the same document twice writes the same
    file rather than a second copy of it.
    """
    return f"{assets_url(document_url_prefix, assets_url_prefix)}{_segment(name)}"


def save(
    document: models.MarkdownDocument,
    document_url_prefix: str,
    assets_url_prefix: str,
) -> str:
    """Store `document` and its pictures, answering where its Markdown was put.

    `assets_url_prefix` is what the stored document calls its pictures, and it is
    written into the file as it stands. Where their bytes go is the one thing worked
    out rather than told: a relative prefix resolves against `document_url_prefix`,
    an absolute one names its own place. So a caller says `./assets` for a guide that
    is one movable directory and a bucket URL for pictures kept apart from it, and
    says it once either way.

    The pictures go first. A document naming a picture that is not there yet is a
    broken document for as long as the gap lasts, and the gap is avoidable by
    ordering the writes.
    """
    into = assets_url(document_url_prefix, assets_url_prefix)
    for image in _unique(document.images):
        _save_asset(image, into)

    url = content_url(document_url_prefix)
    markdown = document.markdown(_directory(assets_url_prefix))
    _write(url, markdown.encode("utf-8"), _MARKDOWN)
    return url


def load(document_url_prefix: str) -> models.MarkdownDocument | None:
    """The guide stored there as the model that wrote it, or None if there is none.

    Takes no account of where the pictures went: a name is the last segment of
    whatever the document points at, so this reads a document naming them relatively
    and one naming them by full URL alike.

    "No such guide" is an ordinary answer to an ordinary question, so it is an answer
    rather than an exception a caller has to know to catch.
    """
    stored = read(content_url(document_url_prefix))
    if stored is None:
        return None

    return reader.from_markdown(stored.decode("utf-8"))


def read(url: str) -> bytes | None:
    """The bytes at `url`, or None where there is nothing there.

    Public because not everything this service reads is a guide: the .docx a guide
    is converted from is an object in someone else's layout, and a picture a stored
    document names absolutely is reached by the address the document gives rather
    than by working out where it ought to be.
    """
    return _read(url)


def load_asset(
    document_url_prefix: str, assets_url_prefix: str, name: str
) -> bytes | None:
    """The bytes of one of a guide's pictures.

    Takes the same pair `save` was given, so that reading a picture back never asks
    a caller to resolve an address the store resolved when it wrote it.

    Fetched only when something asks. A document read back carries its pictures by
    name, which is all that rendering it needs.
    """
    return _read(asset_url(document_url_prefix, assets_url_prefix, name))


def _save_asset(image: models.Image, into: str) -> None:
    """Write one picture, where its bytes are here to write.

    A picture read back out of a store carries no bytes - they are already in it,
    under this very name - and writing it again would put an empty file over a good
    one.
    """
    if image.data is None:
        return

    _write(f"{into}{_segment(image.name)}", image.data, image.content_type)


def _unique(images: list[models.Image]) -> list[models.Image]:
    """One entry per picture.

    A section records a picture once per place it is drawn, and the same picture drawn
    twice is one file to write. It is the same file either way - the name is the
    digest of the bytes - so this saves the work rather than preventing a mistake.
    """
    return list({image.name: image for image in images}.values())


def _directory(url: str) -> str:
    """`url` as a prefix that can be concatenated: with its trailing slash."""
    return url if url.endswith("/") else f"{url}/"


def _segment(name: str) -> str:
    """`name` as one segment of a URL.

    A guide is usually identified by something already safe to write in a URL, and
    the tooling names one after the document it converted - which holds spaces, and
    could hold a `#` that would truncate every URL built from it. Escaping here
    rather than asking callers to is what lets an id be whatever named the guide.
    """
    return urllib.parse.quote(name, safe="")


def _read(url: str) -> bytes | None:
    """The bytes at `url`, or None where there is nothing there."""
    scheme, location = _reached(url)
    return _READERS[scheme](location)


def _write(url: str, data: bytes, content_type: str) -> None:
    """Put `data` at `url`, making whatever has to exist to hold it.

    `content_type` is carried because a stored document names its pictures by URL
    and something else will fetch them: a picture answered as
    `application/octet-stream` is one a browser will not draw. A filesystem has
    nowhere to record it and drops it; a bucket keeps it.
    """
    scheme, location = _reached(url)
    _WRITERS[scheme](location, data, content_type)


def _reached(url: str) -> tuple[str, urllib.parse.SplitResult]:
    """`url`'s scheme and its parts, or a refusal naming what can be reached.

    The one place a URL becomes a way of reaching something. Everything above it
    works in guides and pictures and says where they are; only this says how to get
    there, which is what lets a second scheme be a pair of functions rather than a
    second store.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _READERS:
        message = (
            f"Cannot reach {url!r}: this store speaks "
            f"{', '.join(f'{scheme}://' for scheme in sorted(_READERS))} "
            f"and nothing else."
        )
        raise UnsupportedSchemeError(message)

    return parsed.scheme, parsed


def _file_path(location: urllib.parse.SplitResult) -> Path:
    """The file a `file://` URL names.

    `url2pathname` rather than taking `.path` as it stands, because a URL escapes
    the characters a path is allowed to contain and a guide's directory is named by
    whatever minted its id.
    """
    return Path(urllib.request.url2pathname(location.path))


def _read_file(location: urllib.parse.SplitResult) -> bytes | None:
    path = _file_path(location)
    return path.read_bytes() if path.is_file() else None


def _write_file(
    location: urllib.parse.SplitResult, data: bytes, _content_type: str
) -> None:
    """A file has no room for a type of its own: its name carries the extension,
    and whatever serves it decides from that."""
    path = _file_path(location)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _bucket_and_key(location: urllib.parse.SplitResult) -> tuple[str, str]:
    """The bucket and key an `s3://` URL names.

    The bucket is the host and the key is the path without its leading slash, and
    the key is unquoted for the same reason a file path is: what was escaped to
    survive being a URL is not part of the name.
    """
    return location.netloc, urllib.parse.unquote(location.path.lstrip("/"))


def _read_s3(location: urllib.parse.SplitResult) -> bytes | None:
    bucket, key = _bucket_and_key(location)
    try:
        response = _s3().get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in _MISSING:
            return None
        raise

    body: bytes = response["Body"].read()
    return body


def _write_s3(
    location: urllib.parse.SplitResult, data: bytes, content_type: str
) -> None:
    bucket, key = _bucket_and_key(location)
    _s3().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def _s3() -> Any:
    """A client pointed at floci where one is configured, and at AWS where not."""
    settings = config.get_config()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.floci_endpoint_url,
    )


# How each scheme is reached. Adding one is a pair of functions and an entry here,
# because nothing above knows a scheme exists.
_READERS: dict[str, Callable[[urllib.parse.SplitResult], bytes | None]] = {
    "file": _read_file,
    "s3": _read_s3,
}

_WRITERS: dict[str, Callable[[urllib.parse.SplitResult, bytes, str], None]] = {
    "file": _write_file,
    "s3": _write_s3,
}
