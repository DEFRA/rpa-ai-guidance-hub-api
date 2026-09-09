"""Where a converted guide is kept, and how it is got back.

A guide lives at a URL, and holds one Markdown file and the pictures it draws:

    <guide url>/content.md
    <guide url>/assets/<digest>.<ext>

That URL is the whole of what a caller has to say. Its scheme says how the guide is
reached - `file://` for a directory, `s3://` for a bucket - and storing is the same
operation either way: the layout above, the names, the order the writes go in and
what the document says about itself do not change with the medium, so none of them
are written down twice.

Nothing here knows that a guide has an *id*. Keeping guides as `<base>/<id>` is one
way to arrange them and `guide_url` composes that, but it is the caller's arrangement
rather than this module's, and a guide put anywhere else is stored and read the same.

The Markdown is standalone. Its image paths are relative - `assets/a3f9....png` - so
they resolve against wherever the document itself is, and its cross-references point
at anchors that headings in the same file print. Anything able to read Markdown can
read a stored guide with nothing else, and `reader.from_markdown` reads it back into
the model that wrote it.

**Relative paths inside, an absolute URL outside, and the split is the point.** A
document that named its pictures `s3://a-bucket/...` or `file:///home/...` would carry
a bucket name or a machine's directory layout in its text - configuration, written
into a file that outlives it, and wrong the moment the guide is copied anywhere. A
relative path names nothing but the document's own neighbourhood. Where that
neighbourhood *is* belongs to the caller, and is what `base` says.
"""

from __future__ import annotations

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

# What a stored document's image paths are written against, and read back off. A
# trailing slash because it is concatenated with a bare name rather than joined.
ASSET_PREFIX = "assets/"

# What S3 says when an object is not there. A missing *bucket* is deliberately not
# among them: it means this service is pointed somewhere that does not exist, and
# answering "no such guide" to every question is how a mistyped bucket name looks
# exactly like an empty store.
_MISSING = ("NoSuchKey", "404")


class UnsupportedSchemeError(ValueError):
    """Raised for a location this cannot reach."""


def guide_url(base: str, guide_id: str) -> str:
    """The URL of the guide `guide_id` names beneath `base`.

    A convenience for callers that keep guides as `<base>/<id>`, and the one place
    an id is escaped: the dev tooling names a guide after the document it converted,
    and those hold spaces - a `#` in one would truncate every URL built from it.
    """
    return f"{_directory(base)}{_segment(guide_id)}"


def content_url(guide: str) -> str:
    """Where a guide's Markdown lives: one file per guide."""
    return f"{_directory(guide)}{_CONTENT}"


def assets_url(guide: str) -> str:
    """Where a guide keeps its pictures.

    Built from `ASSET_PREFIX` against the guide's own URL, which is the statement
    that the relative path a stored document carries resolves to exactly this: the
    two cannot drift, because there is only one of them.
    """
    return f"{_directory(guide)}{ASSET_PREFIX}"


def asset_url(guide: str, name: str) -> str:
    """Where one of a guide's pictures lives.

    Beside the document that draws it, and named for its content, so that converting
    the same document twice writes the same file rather than a second copy of it.
    """
    return f"{assets_url(guide)}{_segment(name)}"


def save(
    document: models.MarkdownDocument, guide: str, assets: str | None = None
) -> str:
    """Store `document` at `guide`, answering where its Markdown was put.

    `assets` is where the pictures go and what the stored document addresses them
    by, and defaults to `assets/` beneath the guide. Give it an absolute URL - a
    bucket of its own, say - and the stored file names its pictures absolutely,
    which is what lets the file be read anywhere without being told where it came
    from. Give it nothing and the addresses stay relative, which is what lets the
    whole guide be moved or copied as a directory.

    The pictures go first. A document naming a picture that is not there yet is a
    broken document for as long as the gap lasts, and the gap is avoidable by
    ordering the writes.
    """
    prefix = _directory(assets) if assets else ASSET_PREFIX
    into = assets if assets else assets_url(guide)

    for image in _unique(document.images):
        _save_asset(image, into)

    url = content_url(guide)
    _write(url, document.markdown(prefix).encode("utf-8"), _MARKDOWN)
    return url


def load(guide: str) -> models.MarkdownDocument | None:
    """The guide at `guide` as the model that wrote it, or None if there is none.

    Takes no account of where the pictures went: a name is the last segment of
    whatever the document points at, so this reads a document addressing them
    relatively and one addressing them absolutely alike.

    "No such guide" is an ordinary answer to an ordinary question, so it is an answer
    rather than an exception a caller has to know to catch.
    """
    stored = read(content_url(guide))
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


def _save_asset(image: models.Image, into: str) -> None:
    """Write one picture, where its bytes are here to write.

    A picture read back out of a store carries no bytes - they are already in it,
    under this very name - and writing it again would put an empty file over a good
    one.
    """
    if image.data is None:
        return

    _write(f"{_directory(into)}{_segment(image.name)}", image.data, image.content_type)


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
