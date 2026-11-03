"""Turning an uploaded .docx into a stored version of a document, and listing them.

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
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app import config
from app.guidance import ids, models, records
from app.guidance.documents import store
from app.guidance.parsing import parser

if TYPE_CHECKING:
    import pymongo.asynchronous.database

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


def read_asset(
    document_id: ids.DocumentId, name: str, s3_client: Any = None
) -> bytes | None:
    """The bytes of one of a document's pictures, or None if it is not there.

    Pictures belong to the document, not to any version of it: see the module
    docstring.
    """
    base = f"s3://{config.get_config().managed_docs_s3_bucket}"
    document = store.document_url(base, str(document_id))
    return store.load_asset(document, "./assets", name, s3_client=s3_client)


class UnknownFilterValueError(ValueError):
    """Raised when a list is narrowed to a value the reference data does not have."""


class GuidanceService:
    def __init__(self, s3_client: Any = None) -> None:
        self._s3 = s3_client

    def convert(
        self,
        source_url: str,
        document_id: ids.DocumentId | None = None,
        version_id: ids.VersionId | None = None,
    ) -> models.StoredDocument:
        return convert(
            source_url,
            document_id=document_id,
            version_id=version_id,
            s3_client=self._s3,
        )


def convert(
    source_url: str,
    document_id: ids.DocumentId | None = None,
    version_id: ids.VersionId | None = None,
    s3_client: Any = None,
) -> models.StoredDocument:
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

    source = store.read(source_url, s3_client=s3_client)
    if source is None:
        message = f"No document at {source_url}"
        raise SourceMissingError(message)

    document = parser.parse_docx(source)

    document_id = document_id or ids.new_document_id()
    version_id = version_id or ids.new_version_id()

    into = store.version_url(
        f"s3://{settings.managed_docs_s3_bucket}", document_id, version_id
    )

    content = store.save(document, into, _ASSETS, s3_client=s3_client)

    return models.StoredDocument(
        document_id=document_id,
        version_id=version_id,
        content=content,
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


# The filters that list, for each of their values, how many guides have it.
COUNTED_FILTERS = ("scheme", "system", "audience")

# The details whose counts include the guides with none of their values: a guide
# specific to no scheme has none, and is a choice of its own.
_COUNTING_NONE = ("schemes",)


def _filters() -> dict[str, tuple[str, list[dict[str, str]]]]:
    """Each filter's name, the metadata key it narrows, and its reference data."""
    reference = config.get_config()
    return {
        "scheme": ("schemes", reference.reference_schemes),
        "system": ("systems", reference.reference_systems),
        "audience": ("audience", reference.reference_audiences),
        "guidance_type": ("guidanceType", reference.reference_guidance_types),
    }


def chosen_filters(
    given: Mapping[str, Sequence[str] | None], *, no_scheme: bool
) -> dict[str, list[str | None]]:
    """The filters given, by name, with None among the schemes for the guides
    specific to no scheme."""
    chosen: dict[str, list[str | None]] = {
        name: list(values) for name, values in given.items() if values
    }
    if no_scheme:
        chosen["scheme"] = [*chosen.get("scheme", []), None]
    return chosen


def _metadata_filters(
    requested: Mapping[str, Sequence[str | None]],
) -> dict[str, list[str | None]]:
    """Map filter names to the metadata keys they narrow, checking each value.
    None is the guides with none of the filter's values.

    Raises:
        UnknownFilterValueError: if a value is not in the reference data.
    """
    known = _filters()

    metadata: dict[str, list[str | None]] = {}
    for name, values in requested.items():
        key, options = known[name]
        allowed = {option["label"] for option in options}
        unknown = sorted({value for value in values if value is not None} - allowed)
        if unknown:
            message = (
                f"Unknown {name} {', '.join(unknown)}; "
                f"expected one of {', '.join(sorted(allowed))}"
            )
            raise UnknownFilterValueError(message)
        metadata[key] = list(values)
    return metadata


async def _option_counts(
    database: pymongo.asynchronous.database.AsyncDatabase,
    metadata: Mapping[str, Sequence[str | None]],
) -> dict[str, list[models.OptionCount]]:
    """For each counted filter, the values the documents have, each with how many
    have it given the other filters chosen, and any value chosen even if none has
    it; in the reference data's order, labelled as it labels them. Guides with no
    scheme are counted after the schemes, with no label.
    """
    known = _filters()
    keys = {name: known[name][0] for name in COUNTED_FILTERS}
    counted = await records.count_values(
        database,
        metadata=metadata,
        keys=list(keys.values()),
        counting_none=_COUNTING_NONE,
    )
    return {
        name: _counted_options(counted[key], metadata.get(key, []), known[name][1])
        for name, key in keys.items()
    }


def _counted_options(
    counted: Mapping[str | None, int],
    chosen: Sequence[str | None],
    options: Sequence[Mapping[str, str]],
) -> list[models.OptionCount]:
    """The values to show for one filter: those counted, and those chosen at 0.
    A value the reference data no longer has comes after those it has, and the
    guides with none of them (None) after that."""
    counts = {**dict.fromkeys(chosen, 0), **counted}
    short_labels: dict[str | None, str | None] = {
        option["label"]: option.get("shortLabel") for option in options
    }
    order = {label: position for position, label in enumerate(short_labels)}
    labels = sorted(
        counts,
        key=lambda label: (label is None, order.get(label, len(order)), label or ""),
    )
    return [
        models.OptionCount(
            label=label, short_label=short_labels.get(label), count=counts[label]
        )
        for label in labels
    ]


# How a list can be sorted, by when each guide last changed: newest first or not.
SORTS = {"updated-newest": True, "updated-oldest": False}


async def list_page(  # noqa: PLR0913 - each is a separate part of the query
    database: pymongo.asynchronous.database.AsyncDatabase,
    *,
    filters: Mapping[str, Sequence[str | None]],
    after: ids.DocumentId | None,
    limit: int,
    sort: str = "updated-newest",
) -> models.DocumentPage:
    """A page of documents, in order of when each last changed, each with its
    newest version, and every filter's values with their counts.

    A document matches a filter if it has any of the values given, and must match
    every filter given. `after` is the last document of the previous page.

    Raises:
        UnknownFilterValueError: if a filter value is not in the reference data.
        records.UnknownCursorError: if `after` is not a document.
    """
    metadata = _metadata_filters(filters)

    # One more than a page, to know whether there is another.
    found = await records.list_documents(
        database,
        metadata=metadata,
        after=after,
        limit=limit + 1,
        newest_first=SORTS[sort],
    )
    documents = found[:limit]
    latest = await records.latest_versions_of(
        database, [document.id for document in documents]
    )

    return models.DocumentPage(
        items=[
            models.DocumentSummary(document, latest.get(document.id))
            for document in documents
        ],
        next_cursor=documents[-1].id if len(found) > limit else None,
        counts=await _option_counts(database, metadata),
    )
