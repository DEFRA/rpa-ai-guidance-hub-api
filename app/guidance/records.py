"""The record of a document: what it is, and every version of it there has been.

Two collections, because two different things change at two different rates.

`documents` holds what a document *is* to the people using it - the title an author
typed, who it is for, who owns it - and where it came from. The upload is the
document's own: one .docx became one document, and every version after the first is
an edit of that rather than another upload of it.

`document_versions` holds what one conversion produced: where its Markdown went, and
who was signed in when it was made. One document has many, and the newest is only the
newest by `createdAt`. A version id is a time-ordered uuid, but nothing reads meaning
into its order.

**A version records its content URL and nothing else about where it is.** That URL is
enough to restore the document, and the pictures are named by the document itself, in
addresses relative to that very URL - so a second field pointing at them would be a
copy of something the file already says, free to disagree with it.

The title is the exception, and deliberately so. It is copied out of the document the
version stored, and reading a version back takes the title from the content rather
than from here - so this field is never the answer to "what is it called?". What it
answers is "what was it called *then*", which nothing else can: a document retitled
between versions leaves a trail here, and the copy differing from the content is the
whole of the information rather than a fault in it.

The ids are the ones the object store uses. `documents._id` is the prefix a document
is written under and a version's `_id` is the prefix beneath that, so a record and a
key are two spellings of one address rather than two facts to keep in step. They are
stored as native uuids, and the prefixes are their text (see `ids`).

**Records are created, never replaced, and one already there is kept.** A new
document's ids are reserved once, on its upload's staging record, so the only thing
that can already hold one of them is an earlier attempt at the same document. Each
insert is whole or absent, so finding the record means that attempt got this far,
and the record found is the answer. A version found under a different document is
not that, and is raised.

**An owner and a version's creator are not the same thing and are not stored
together.** An owner is metadata: a person or a team named on the form, possibly
nobody with an account, and there may be several. A version's creator is the
authenticated identity that made *that* version - a machine id from the auth
provider, with a display name kept beside it so a listing can name them without a
directory lookup. Conflating the two would make the answer to "who is responsible for
this?" depend on who last pressed a button.

`uploadId` is unique across versions, and that is the whole of how one upload is
converted once. The journey that produces a version can be submitted twice - a
refresh, a back button, a retried request - and each of those means the same version
rather than another one.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pymongo.errors

from app.guidance import ids, models

if TYPE_CHECKING:
    import pymongo.asynchronous.database

DOCUMENTS = "documents"
VERSIONS = "document_versions"

# The upload a document was converted from, and the only thing that says two requests
# mean the same document.
_UPLOAD_ID = "source.uploadId"

_METADATA = "metadata"

# The document a version belongs to: the many end of the one-to-many. Its name is the
# model's, which owns how a version is stored.
DOCUMENT_ID = models.DOCUMENT_ID


class MisfiledVersionError(RuntimeError):
    """Raised when a version id is already taken by a version of another document."""


# Newest first, by when a version was made. A version id is time-ordered too, but
# `createdAt` is what says which is newest.
_NEWEST = [("createdAt", -1)]

# The aggregation stages the queries below use more than once.
_MATCH = "$match"
_SORT = "$sort"
_IN = "$in"
_GROUP = "$group"
_OR = "$or"


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """Make the indexes the collections rest on.

    Idempotent, and called at startup rather than on the first write: an index that
    only appears once something has been written is an index that was not there for
    whatever raced it.
    """
    await database[DOCUMENTS].create_index(_UPLOAD_ID, unique=True)
    await database[VERSIONS].create_index([(DOCUMENT_ID, 1), *_NEWEST])
    for key in models.FILTERABLE_METADATA:
        await database[DOCUMENTS].create_index(f"{_METADATA}.{key}")


async def find_by_upload(
    database: pymongo.asynchronous.database.AsyncDatabase, upload_id: ids.UploadId
) -> models.Document | None:
    """The document already converted from `upload_id`, if there is one."""
    found = await database[DOCUMENTS].find_one({_UPLOAD_ID: upload_id})
    return None if found is None else models.Document.from_document(found)


async def find(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> models.Document | None:
    """One document by its id, without its versions."""
    found = await database[DOCUMENTS].find_one({"_id": document_id})
    return None if found is None else models.Document.from_document(found)


class UnknownCursorError(LookupError):
    """Raised when a page is asked for after a document that is not there."""


async def list_documents(  # noqa: PLR0913 - each is a separate part of the query
    database: pymongo.asynchronous.database.AsyncDatabase,
    *,
    metadata: Mapping[str, Sequence[str | None]],
    after: ids.DocumentId | None,
    limit: int,
    newest_first: bool = True,
) -> list[models.Document]:
    """Up to `limit` documents, in order of when each last changed, after `after`.

    A document last changed when its newest version was made (or, with none, when
    it was recorded). It matches when, for every metadata key, at least one of its
    values is among those asked for. The cursor is the last document of the page
    before: the page goes on from where that document stands in the order now, ties
    broken by id, so the cursor stays a document id.

    Raises:
        UnknownCursorError: if `after` is not a document.
    """
    direction = -1 if newest_first else 1
    pipeline: list[dict[str, Any]] = [
        *_as_now(),
        *_matching(metadata),
        *_updated_at(),
        *await _after(database, after, direction),
        {_SORT: {_UPDATED_AT: direction, "_id": direction}},
        {"$limit": limit},
    ]
    cursor = await database[DOCUMENTS].aggregate(pipeline)
    return [models.Document.from_document(document) async for document in cursor]


async def count_values(
    database: pymongo.asynchronous.database.AsyncDatabase,
    *,
    metadata: Mapping[str, Sequence[str | None]],
    keys: Sequence[str],
    counting_none: Collection[str] = (),
    only: Sequence[ids.DocumentId] | None = None,
) -> dict[str, dict[str | None, int]]:
    """For each of `keys`, how many documents have each value in their metadata,
    given the other keys' filters in `metadata` but not its own, so a filter's
    counts say what choosing another of its values would find. A document with
    several values counts once under each, however often it gives one.

    For a key in `counting_none`, the documents with none of its values count under
    None. Metadata is stored as sent, so a value that is not words (an object, a
    number) is not counted, rather than breaking the count of every guide.

    Only the documents of `only`, if given.
    """
    some = [] if only is None else [{_MATCH: {"_id": {_IN: list(only)}}}]
    facets = {
        key: [
            *_matching({k: v for k, v in metadata.items() if k != key}),
            {
                "$unwind": {
                    "path": f"${_METADATA}.{key}",
                    "preserveNullAndEmptyArrays": key in counting_none,
                }
            },
            *_countable(f"{_METADATA}.{key}", none_counts=key in counting_none),
            # Once for each document and value, then how many documents per value.
            {_GROUP: {"_id": {"document": "$_id", "value": f"${_METADATA}.{key}"}}},
            {_GROUP: {"_id": "$_id.value", "count": {"$sum": 1}}},
        ]
        for key in keys
    }
    cursor = await database[DOCUMENTS].aggregate(
        [*some, *_as_now(), {"$facet": facets}]
    )
    (counted,) = [result async for result in cursor]
    return {
        key: {group["_id"]: group["count"] for group in counted[key]} for key in keys
    }


def _countable(field: str, *, none_counts: bool) -> list[dict[str, Any]]:
    """The stage keeping only values that are words, and, where `none_counts`, the
    documents with none at all.

    The value's own type, by `$type` in an expression: a query's `$type` would also
    take a list holding words, which cannot be counted.
    """
    types = ["string", "null", "missing"] if none_counts else ["string"]
    return [{_MATCH: {"$expr": {_IN: [{"$type": f"${field}"}, types]}}}]


_SCHEMES = f"{_METADATA}.schemes"


def _as_now() -> list[dict[str, Any]]:
    """The stage reading each document's metadata as now
    (`models.details_as_now`): a guide stored as "Not scheme-specific" has no
    scheme.

    The match after it works on what this works out, so it cannot use the
    metadata indexes.
    """
    return [
        {
            "$set": {
                _SCHEMES: {
                    "$cond": [
                        {"$isArray": f"${_SCHEMES}"},
                        {
                            "$filter": {
                                "input": f"${_SCHEMES}",
                                "cond": {"$ne": ["$$this", models.NOT_SCHEME_SPECIFIC]},
                            }
                        },
                        f"${_SCHEMES}",
                    ]
                }
            }
        }
    ]


def _matching(metadata: Mapping[str, Sequence[str | None]]) -> list[dict[str, Any]]:
    """The stage keeping only documents whose metadata match: none for no filter.

    A key matches when the document has any of its values; None among them is a
    document with none of the key's values at all, an empty list or none given.
    """
    if not metadata:
        return []
    return [
        {_MATCH: {"$and": [_any_of(key, values) for key, values in metadata.items()]}}
    ]


def _any_of(key: str, values: Sequence[str | None]) -> dict[str, Any]:
    """The condition that the document has any of `values` for `key`."""
    field = f"{_METADATA}.{key}"
    named = [value for value in values if value is not None]
    if None not in values:
        return {field: {_IN: named}}
    return {_OR: [{field: {_IN: named}}, {field: {_IN: [[], None]}}]}


# What `_updated_at` works out for each document.
_UPDATED_AT = "updatedAt"


def _updated_at() -> list[dict[str, Any]]:
    """The stages working out when each document last changed: when its newest
    version was made, or, with none, when it was recorded."""
    return [
        {
            "$lookup": {
                "from": VERSIONS,
                "localField": "_id",
                "foreignField": DOCUMENT_ID,
                "pipeline": [
                    {_SORT: dict(_NEWEST)},
                    {"$limit": 1},
                    {"$project": {"createdAt": 1}},
                ],
                "as": "newest",
            }
        },
        {
            "$set": {
                _UPDATED_AT: {
                    "$ifNull": [{"$first": "$newest.createdAt"}, "$createdAt"]
                },
            }
        },
    ]


async def _after(
    database: pymongo.asynchronous.database.AsyncDatabase,
    after: ids.DocumentId | None,
    direction: int,
) -> list[dict[str, Any]]:
    """The stage going on after `after`, where it stands in the order now."""
    if after is None:
        return []

    found = [
        document
        async for document in await database[DOCUMENTS].aggregate(
            [{_MATCH: {"_id": after}}, *_updated_at()]
        )
    ]
    if not found:
        message = f"No document {after} to go on from"
        raise UnknownCursorError(message)

    beyond = "$lt" if direction < 0 else "$gt"
    updated_at = found[0][_UPDATED_AT]
    return [
        {
            _MATCH: {
                _OR: [
                    {_UPDATED_AT: {beyond: updated_at}},
                    {_UPDATED_AT: updated_at, "_id": {beyond: after}},
                ]
            }
        }
    ]


async def matching(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
    metadata: Mapping[str, Sequence[str | None]],
) -> set[ids.DocumentId]:
    """Those of `document_ids` that are documents whose metadata match, by the
    rule `list_documents` follows."""
    pipeline: list[dict[str, Any]] = [
        {_MATCH: {"_id": {_IN: list(document_ids)}}},
        *_as_now(),
        *_matching(metadata),
        {"$project": {"_id": 1}},
    ]
    cursor = await database[DOCUMENTS].aggregate(pipeline)
    return {document["_id"] async for document in cursor}


async def find_many(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> dict[ids.DocumentId, models.Document]:
    """Those of `document_ids` that are documents, by id, in a single query."""
    cursor = database[DOCUMENTS].find({"_id": {_IN: list(document_ids)}})
    return {
        document["_id"]: models.Document.from_document(document)
        async for document in cursor
    }


async def existing(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> set[ids.DocumentId]:
    """Those of `document_ids` that are documents."""
    cursor = database[DOCUMENTS].find({"_id": {_IN: list(document_ids)}}, {"_id": 1})
    return {document["_id"] async for document in cursor}


async def latest_versions_of(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> dict[ids.DocumentId, models.Version]:
    """The newest version of each of `document_ids` that has one, in a single query."""
    pipeline: list[dict[str, Any]] = [
        {_MATCH: {DOCUMENT_ID: {_IN: list(document_ids)}}},
        {_SORT: dict(_NEWEST)},
        {_GROUP: {"_id": f"${DOCUMENT_ID}", "newest": {"$first": "$$ROOT"}}},
    ]
    cursor = await database[VERSIONS].aggregate(pipeline)
    return {
        group["_id"]: models.Version.from_document(group["newest"])
        async for group in cursor
    }


async def versions_of(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> list[models.Version]:
    """Every version of `document_id`, newest first."""
    cursor = database[VERSIONS].find({DOCUMENT_ID: document_id}).sort(_NEWEST)
    return [models.Version.from_document(version) async for version in cursor]


async def latest_version(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> models.Version | None:
    """The most recent version of `document_id`, or None if it has none.

    A document with no versions is a document whose first conversion failed after its
    record was written. It exists, and it has no content, and those are two different
    answers.
    """
    found = await database[VERSIONS].find_one({DOCUMENT_ID: document_id}, sort=_NEWEST)
    return None if found is None else models.Version.from_document(found)


async def create(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document: models.Document,
) -> models.Document:
    """Record a document, answering the record as it was written.

    Its id is the prefix the document is stored under, not an id minted here.

    Its metadata is what the journey collected, owners included: this service does
    not interpret it, because what an author is asked about a document is a question
    for the journey rather than for the store behind it.

    Writing it is what commits the document, so it goes last, after its first
    version. If it is already there, an earlier attempt committed it, and that
    record is answered as it stands.
    """
    stored = await _created(database[DOCUMENTS], document.to_document())
    return models.Document.from_document(stored)


async def create_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
) -> models.Version:
    """Record one version of a document, answering the record as it was written.

    Its id is the prefix this version was stored under, and its content URL names
    the file inside it - the whole of what is needed to read the version back.

    Its title is the document's own title as this version had it, kept so a person
    can see what a version was called without fetching it. See the module docstring
    for why a copy is the point here and would be a fault anywhere else.

    Its creator is who was signed in, not who owns the document - see the module
    docstring. It is optional because the endpoint is not yet authenticated, and a
    version made by nobody is more honest than one attributed to a guess.

    Written only once its content is wholly stored. If it is already there, an
    earlier attempt wrote it, and that record is answered as it stands - unless it
    belongs to another document, which no attempt at this one could have done.

    Raises:
        MisfiledVersionError: if the version id is taken by another document's
            version.
    """
    stored = models.Version.from_document(
        await _created(database[VERSIONS], version.to_document())
    )
    if stored.document_id != version.document_id:
        message = (
            f"Version {version.id} belongs to document {stored.document_id}, "
            f"not {version.document_id}"
        )
        raise MisfiledVersionError(message)
    return stored


async def _created(collection: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Insert `record`, or answer the one already holding its `_id`.

    Only a clash on `_id` is an earlier attempt's work. A clash on another unique
    index - an upload already made into a different document - has no record under
    this id, and is raised as it was.
    """
    try:
        await collection.insert_one(record)
    except pymongo.errors.DuplicateKeyError:
        existing: dict[str, Any] | None = await collection.find_one(
            {"_id": record["_id"]}
        )
        if existing is None:
            raise
        return existing
    return record
