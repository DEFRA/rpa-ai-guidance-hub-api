"""The record of a document: what it is, and every version of it there has been.

Two collections, because two different things change at two different rates.

`documents` holds what a document *is* to the people using it - the title an author
typed, who it is for, who owns it. None of that is a property of any one conversion,
and re-converting must not disturb it.

`document_versions` holds what a conversion produced: where the Markdown went, the
upload it came from, and who was signed in when it was made. One document has many,
and the newest is only the newest by `createdAt` - a version id is a uuid and says
nothing about order.

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

import datetime as dt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pymongo.asynchronous.database

DOCUMENTS = "documents"
VERSIONS = "document_versions"

# The upload a version was converted from, and the only thing that says two requests
# mean the same version.
_UPLOAD_ID = "source.uploadId"

# The document a version belongs to: the many end of the one-to-many. Public because
# a version record read back is how a caller gets from an upload to its document.
DOCUMENT_ID = "documentId"

# Newest first. A version id is a uuid, so when a version was made is the only thing
# that orders one against another.
_NEWEST = [("createdAt", -1)]


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """Make the indexes the collections rest on.

    Idempotent, and called at startup rather than on the first write: an index that
    only appears once something has been written is an index that was not there for
    whatever raced it.
    """
    await database[VERSIONS].create_index(_UPLOAD_ID, unique=True)
    await database[VERSIONS].create_index([(DOCUMENT_ID, 1), *_NEWEST])


async def find_version_by_upload(
    database: pymongo.asynchronous.database.AsyncDatabase, upload_id: str
) -> dict[str, Any] | None:
    """The version already converted from `upload_id`, if there is one."""
    found: dict[str, Any] | None = await database[VERSIONS].find_one(
        {_UPLOAD_ID: upload_id}
    )
    return found


async def find(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: str
) -> dict[str, Any] | None:
    """One document by its id, without its versions."""
    found: dict[str, Any] | None = await database[DOCUMENTS].find_one(
        {"_id": document_id}
    )
    return found


async def versions_of(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: str
) -> list[dict[str, Any]]:
    """Every version of `document_id`, newest first."""
    cursor = database[VERSIONS].find({DOCUMENT_ID: document_id}).sort(_NEWEST)
    return [version async for version in cursor]


async def latest_version(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: str
) -> dict[str, Any] | None:
    """The most recent version of `document_id`, or None if it has none.

    A document with no versions is a document whose first conversion failed after its
    record was written. It exists, and it has no content, and those are two different
    answers.
    """
    found: dict[str, Any] | None = await database[VERSIONS].find_one(
        {DOCUMENT_ID: document_id}, sort=_NEWEST
    )
    return found


async def create(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_id: str,
    *,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Record a document, answering the record as it was written.

    `metadata` is what the journey collected, owners included: this service does not
    interpret it, because what an author is asked about a document is a question for
    the journey rather than for the store behind it.
    """
    record = {
        "_id": document_id,
        "metadata": metadata,
        "createdAt": dt.datetime.now(tz=dt.UTC),
    }
    await database[DOCUMENTS].insert_one(record)
    return record


async def create_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_id: str,
    *,
    document_id: str,
    source: dict[str, Any],
    content: dict[str, Any],
    created_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one version of a document, answering the record as it was written.

    `created_by` is who was signed in, not who owns the document - see the module
    docstring. It is optional because the endpoint is not yet authenticated, and a
    version made by nobody is more honest than one attributed to a guess.
    """
    record = {
        "_id": version_id,
        DOCUMENT_ID: document_id,
        "source": source,
        "content": content,
        "createdBy": created_by,
        "createdAt": dt.datetime.now(tz=dt.UTC),
    }
    await database[VERSIONS].insert_one(record)
    return record
