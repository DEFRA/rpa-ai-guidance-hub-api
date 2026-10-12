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

# The document a version belongs to: the many end of the one-to-many. Its name is the
# model's, which owns how a version is stored.
DOCUMENT_ID = models.DOCUMENT_ID


class MisfiledVersionError(RuntimeError):
    """Raised when a version id is already taken by a version of another document."""


# Newest first, by when a version was made. A version id is time-ordered too, but
# `createdAt` is what says which is newest.
_NEWEST = [("createdAt", -1)]


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
