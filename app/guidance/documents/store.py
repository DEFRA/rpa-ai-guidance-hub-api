"""Where a converted guide is kept, and how it is got back.

A guide lives at a URL, and holds one Markdown file and the pictures it draws:

    <guide url>/content.md
    <guide url>/assets/<digest>.<ext>

That URL is the whole of what a caller has to say. Its scheme says how the guide is
reached - `file://` today, `s3://` when there is something asking for that - and
storing is the same operation either way: the layout above, the names, the order the
writes go in and what the document says about itself do not change with the medium,
so none of them are written down twice.

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

from app.guidance.documents import reader
from app.guidance.parsing import models

_CONTENT = "content.md"

# What a stored document's image paths are written against, and read back off. A
# trailing slash because it is concatenated with a bare name rather than joined.
ASSET_PREFIX = "assets/"

# The schemes this knows how to reach. Named in the error rather than left to a
# stack trace, because getting one wrong is a configuration mistake and the fix is
# to write a different URL.
_SCHEMES = ("file",)


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


def save(document: models.MarkdownDocument, guide: str) -> str:
    """Store `document` at `guide`, answering where its Markdown was put.

    The pictures go first. A document naming a picture that is not there yet is a
    broken document for as long as the gap lasts, and the gap is avoidable by
    ordering the writes.
    """
    for image in _unique(document.images):
        _save_asset(image, guide)

    url = content_url(guide)
    _write(url, document.markdown(ASSET_PREFIX).encode("utf-8"))
    return url


def load(guide: str) -> models.MarkdownDocument | None:
    """The guide at `guide` as the model that wrote it, or None if there is none.

    "No such guide" is an ordinary answer to an ordinary question, so it is an answer
    rather than an exception a caller has to know to catch.
    """
    stored = _read(content_url(guide))
    if stored is None:
        return None

    return reader.from_markdown(stored.decode("utf-8"), ASSET_PREFIX)


def load_asset(guide: str, name: str) -> bytes | None:
    """The bytes of one of a guide's pictures.

    Fetched only when something asks. A document read back carries its pictures by
    name, which is all that rendering it needs.
    """
    return _read(asset_url(guide, name))


def _save_asset(image: models.Image, guide: str) -> None:
    """Write one picture, where its bytes are here to write.

    A picture read back out of a store carries no bytes - they are already in it,
    under this very name - and writing it again would put an empty file over a good
    one.
    """
    if image.data is None:
        return

    _write(asset_url(guide, image.name), image.data)


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
    path = _local_path(url)
    return path.read_bytes() if path.is_file() else None


def _write(url: str, data: bytes) -> None:
    """Put `data` at `url`, making whatever has to exist to hold it."""
    path = _local_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _local_path(url: str) -> Path:
    """The file `url` names.

    The one place a URL becomes a way of reaching something, and so the one place a
    second scheme would be answered. `url2pathname` rather than taking `.path` as it
    stands, because a URL escapes the characters a path is allowed to contain and a
    guide's directory is named by whatever minted its id.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _SCHEMES:
        message = (
            f"Cannot reach {url!r}: this store speaks "
            f"{', '.join(f'{scheme}://' for scheme in _SCHEMES)} and nothing else."
        )
        raise UnsupportedSchemeError(message)

    return Path(urllib.request.url2pathname(parsed.path))
