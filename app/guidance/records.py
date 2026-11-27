"""The records of a document and its versions, in two collections.

`documents` holds the upload a document's first version came from; that upload makes
one document only (`uploadId` is unique). `document_versions` holds each conversion:
where its Markdown went, the details given on the form with it, and who was signed in
when it was made. The newest version is the newest by `createdAt`, and a document's
details are its newest version's; a document keeps its own only for a version saved
before versions kept theirs.

A record's ids are the object store's prefixes for its content (see `ids`). A record
is created, never replaced: its ids are reserved on the upload's staging record, so a
record already there is from an earlier attempt at the same save, and is the answer.

A version's title is the one the maintainer confirmed, which starts as its content's
own: what the guide was called then. An owner is a detail given on the form; a
version's creator is the identity that was signed in.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pymongo.errors

from app.common import mongo_timeouts
from app.guidance import ids, models

if TYPE_CHECKING:
    import pymongo.asynchronous.database

DOCUMENTS = "documents"
VERSIONS = "document_versions"

# The upload a document was converted from, and the only thing that says two requests
# mean the same document.
_UPLOAD_ID = "source.uploadId"

_METADATA = "metadata"
_TITLE = "title"
# The canonical link of the document a version came from, by which links in guides
# name the guide.
_CANONICAL_SOURCE_LINK = "sourceLink.canonicalLink"

# The document a version belongs to: the many end of the one-to-many. Its name is the
# model's, which owns how a version is stored.
DOCUMENT_ID = models.DOCUMENT_ID


class MisfiledVersionError(RuntimeError):
    """Raised when a version id is already taken by a version of another document."""


class OvertakenVersionError(RuntimeError):
    """Raised when another version replaced the version this one replaces first."""


# Newest first, by when a version was made. A version id is time-ordered too, but
# `createdAt` is what says which is newest.
_NEWEST = [("createdAt", -1)]

# The aggregation stages and operators the queries below use more than once.
_MATCH = "$match"
_SORT = "$sort"
_IN = "$in"
_GROUP = "$group"
_OR = "$or"
_FIRST = "$first"
_LIMIT = "$limit"
_PROJECT = "$project"


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
    # One version replaces each.
    await database[VERSIONS].create_index(
        [(DOCUMENT_ID, 1), (models.FOLLOWS, 1)],
        unique=True,
        partialFilterExpression={models.FOLLOWS: {"$exists": True}},
    )
    for key in models.FILTERABLE_METADATA:
        await database[DOCUMENTS].create_index(f"{_METADATA}.{key}")
    await database[VERSIONS].create_index(
        _CANONICAL_SOURCE_LINK,
        partialFilterExpression={_CANONICAL_SOURCE_LINK: {"$exists": True}},
    )


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def find_by_upload(
    database: pymongo.asynchronous.database.AsyncDatabase, upload_id: ids.UploadId
) -> models.Document | None:
    """The document already converted from `upload_id`, if there is one."""
    found = await database[DOCUMENTS].find_one({_UPLOAD_ID: upload_id})
    return None if found is None else models.Document.from_document(found)


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def file_saved_as_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_id: ids.DocumentId,
    upload_id: ids.UploadId,
) -> ids.FileId | None:
    """The file of `upload_id` saved as a version of `document_id`, if one was."""
    found = await database[VERSIONS].find_one(
        {DOCUMENT_ID: document_id, _UPLOAD_ID: upload_id}
    )
    return None if found is None else models.Version.from_document(found).file_id


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def find(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> models.Document | None:
    """One document by its id, without its versions, with its details as
    `_latest_details` gives them: its latest version's, or its own where that version
    kept none."""
    found = await database[DOCUMENTS].find_one({"_id": document_id})
    if found is None:
        return None
    document = models.Document.from_document(found)
    latest = await latest_version(database, document_id)
    if latest is None or latest.metadata is None:
        return document
    return dataclasses.replace(document, metadata=dict(latest.metadata))


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def find_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_id: ids.DocumentId,
    version_id: ids.VersionId,
) -> models.Version | None:
    """A version of `document_id` by its id, if it is recorded.

    Raises:
        MisfiledVersionError: if the version id is taken by another document's
            version.
    """
    found = await database[VERSIONS].find_one({"_id": version_id})
    return (
        None
        if found is None
        else _filed(models.Version.from_document(found), document_id)
    )


class UnknownCursorError(LookupError):
    """Raised when a page is asked for after a document that is not there."""


@mongo_timeouts.mongo(mongo_timeouts.HEAVY)
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
        {_LIMIT: limit},
    ]
    cursor = await database[DOCUMENTS].aggregate(pipeline)
    return [models.Document.from_document(document) async for document in cursor]


@mongo_timeouts.mongo(mongo_timeouts.HEAVY)
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
    """The stages reading each document's details as now: its latest version's, read
    as `models.details_as_now` reads them (a guide stored as "Not scheme-specific"
    has no scheme).

    The match after them works on what they work out, so it cannot use the
    metadata indexes.
    """
    return [
        *_latest_details(),
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
        },
    ]


# Where `_latest_details` puts each document's latest version while it reads it.
_LATEST = "latest"


def _latest_details() -> list[dict[str, Any]]:
    """The stages giving each document its latest version's details, or its own where
    that version kept none: one saved before versions kept their own."""
    return [
        {
            "$lookup": {
                "from": VERSIONS,
                "localField": "_id",
                "foreignField": DOCUMENT_ID,
                "pipeline": [
                    {_SORT: dict(_NEWEST)},
                    {_LIMIT: 1},
                    {_PROJECT: {_METADATA: 1}},
                ],
                "as": _LATEST,
            }
        },
        {
            "$set": {
                _METADATA: {
                    "$ifNull": [{_FIRST: f"${_LATEST}.{_METADATA}"}, f"${_METADATA}"]
                }
            }
        },
        {"$unset": _LATEST},
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
                    {_LIMIT: 1},
                    {_PROJECT: {"createdAt": 1}},
                ],
                "as": "newest",
            }
        },
        {
            "$set": {
                _UPDATED_AT: {"$ifNull": [{_FIRST: "$newest.createdAt"}, "$createdAt"]},
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


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
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
        {_PROJECT: {"_id": 1}},
    ]
    cursor = await database[DOCUMENTS].aggregate(pipeline)
    return {document["_id"] async for document in cursor}


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def find_many(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> dict[ids.DocumentId, models.Document]:
    """Those of `document_ids` that are documents, by id, each with its latest
    version's details, in a single query."""
    cursor = await database[DOCUMENTS].aggregate(
        [{_MATCH: {"_id": {_IN: list(document_ids)}}}, *_latest_details()]
    )
    return {
        document["_id"]: models.Document.from_document(document)
        async for document in cursor
    }


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def existing(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> set[ids.DocumentId]:
    """Those of `document_ids` that are documents."""
    cursor = database[DOCUMENTS].find({"_id": {_IN: list(document_ids)}}, {"_id": 1})
    return {document["_id"] async for document in cursor}


@mongo_timeouts.mongo(mongo_timeouts.HEAVY)
async def latest_versions_of(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> dict[ids.DocumentId, models.Version]:
    """The newest version of each of `document_ids` that has one, in a single query."""
    pipeline: list[dict[str, Any]] = [
        {_MATCH: {DOCUMENT_ID: {_IN: list(document_ids)}}},
        {_SORT: dict(_NEWEST)},
        {_GROUP: {"_id": f"${DOCUMENT_ID}", "newest": {_FIRST: "$$ROOT"}}},
    ]
    cursor = await database[VERSIONS].aggregate(pipeline)
    return {
        group["_id"]: models.Version.from_document(group["newest"])
        async for group in cursor
    }


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def guides_known_by(
    database: pymongo.asynchronous.database.AsyncDatabase,
    canonical_links: Collection[str],
) -> dict[str, ids.DocumentId]:
    """The guide each of `canonical_links` names, where one does.

    A guide is known by the source link of any of its versions, so that a new source
    link does not break the links to an earlier one. When two guides have held a link,
    the guide whose latest version has it names it; when neither has it in its latest
    version, the guide that held it last does.
    """
    if not canonical_links:
        return {}
    cursor = (
        database[VERSIONS]
        .find(
            {_CANONICAL_SOURCE_LINK: {_IN: list(canonical_links)}},
            {DOCUMENT_ID: 1, _CANONICAL_SOURCE_LINK: 1},
        )
        .sort(_NEWEST)
    )
    known: dict[str, ids.DocumentId] = {}
    holders: set[ids.DocumentId] = set()
    async for held in cursor:
        known.setdefault(held["sourceLink"]["canonicalLink"], held[DOCUMENT_ID])
        holders.add(held[DOCUMENT_ID])

    latest = await latest_versions_of(database, list(holders))
    for guide, version in sorted(latest.items(), key=lambda each: each[1].created_at):
        if (
            version.source_link is not None
            and version.source_link.canonical_link in known
        ):
            known[version.source_link.canonical_link] = guide
    return known


@mongo_timeouts.mongo(mongo_timeouts.HEAVY)
async def latest_titles(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> dict[ids.DocumentId, str]:
    """The title of every document's newest version, where it has one, in a single
    query."""
    pipeline: list[dict[str, Any]] = [
        {_SORT: dict(_NEWEST)},
        {_GROUP: {"_id": f"${DOCUMENT_ID}", _TITLE: {_FIRST: f"${_TITLE}"}}},
        {_MATCH: {_TITLE: {"$type": "string"}}},
    ]
    cursor = await database[VERSIONS].aggregate(pipeline)
    return {group["_id"]: group[_TITLE] async for group in cursor}


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def versions_of(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: ids.DocumentId
) -> list[models.Version]:
    """Every version of `document_id`, newest first."""
    cursor = database[VERSIONS].find({DOCUMENT_ID: document_id}).sort(_NEWEST)
    return [models.Version.from_document(version) async for version in cursor]


@mongo_timeouts.mongo(mongo_timeouts.STANDARD)
async def versions_of_each(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_ids: Sequence[ids.DocumentId],
) -> dict[ids.DocumentId, list[models.Version]]:
    """Every version of each of `document_ids` that has one, newest first, in a
    single query."""
    cursor = (
        database[VERSIONS].find({DOCUMENT_ID: {_IN: list(document_ids)}}).sort(_NEWEST)
    )
    each: dict[ids.DocumentId, list[models.Version]] = {}
    async for found in cursor:
        version = models.Version.from_document(found)
        each.setdefault(version.document_id, []).append(version)
    return each


@mongo_timeouts.mongo(mongo_timeouts.FAST)
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


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def create(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document: models.Document,
) -> models.Document:
    """Record a document, answering the record as it was written.

    Its id is the prefix the document is stored under, not an id minted here.

    Writing it is what commits the document, so it goes last, after its first
    version. If it is already there, an earlier attempt committed it, or it is the
    document a later version is added to, and that record is answered as it stands.
    """
    stored = await _created(database[DOCUMENTS], document.to_document())
    return models.Document.from_document(stored)


@mongo_timeouts.mongo(mongo_timeouts.FAST)
async def create_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version: models.Version,
) -> models.Version:
    """Record one version of a document, answering the record as it was written.

    Its id is the prefix this version was stored under, and its content URL names
    the file inside it - the whole of what is needed to read the version back.

    Its title is the one the maintainer confirmed for it, kept so a person can see
    what a version was called without fetching it. Its details are what the journey
    collected, owners included: this service does not interpret them, because what
    an author is asked about a document is a question for the journey rather than
    for the store behind it.

    Its creator is who was signed in, not who owns the document - see the module
    docstring. It is optional because the endpoint is not yet authenticated, and a
    version made by nobody is more honest than one attributed to a guess.

    Written only once its content is wholly stored. If it is already there, an
    earlier attempt wrote it, and that record is answered as it stands - unless it
    belongs to another document, which no attempt at this one could have done.

    One version replaces each: a second version that replaces the same one is raised.

    Raises:
        MisfiledVersionError: if the version id is taken by another document's
            version.
        OvertakenVersionError: if another version replaced the one this replaces.
    """
    try:
        created = await _created(database[VERSIONS], version.to_document())
    except pymongo.errors.DuplicateKeyError as clash:
        message = f"Another version replaced {version.follows} before {version.id} did"
        raise OvertakenVersionError(message) from clash
    return _filed(models.Version.from_document(created), version.document_id)


def _filed(version: models.Version, document_id: ids.DocumentId) -> models.Version:
    """`version`, if it is a version of `document_id`; else raises
    `MisfiledVersionError`."""
    if version.document_id != document_id:
        message = (
            f"Version {version.id} belongs to document {version.document_id}, "
            f"not {document_id}"
        )
        raise MisfiledVersionError(message)
    return version


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
