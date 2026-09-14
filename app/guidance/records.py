"""The record of a document: what it is, and every version of it there has been.

Two collections, because two different things change at two different rates.

`documents` holds what a document *is* to the people using it - the title an author
typed, who it is for, who owns it - and where it came from. The upload is the
document's own: one .docx became one document, and every version after the first is
an edit of that rather than another upload of it.

`document_versions` holds what one conversion produced: where its Markdown went, and
who was signed in when it was made. One document has many, and the newest is only the
newest by `createdAt` - a version id is a uuid and says nothing about order.

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
key are two spellings of one address rather than two facts to keep in step.

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

# The upload a document was converted from, and the only thing that says two requests
# mean the same document.
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
    await database[DOCUMENTS].create_index(_UPLOAD_ID, unique=True)
    await database[VERSIONS].create_index([(DOCUMENT_ID, 1), *_NEWEST])


async def find_by_upload(
    database: pymongo.asynchronous.database.AsyncDatabase, upload_id: str
) -> dict[str, Any] | None:
    """The document already converted from `upload_id`, if there is one."""
    found: dict[str, Any] | None = await database[DOCUMENTS].find_one(
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
    source: dict[str, Any],
) -> dict[str, Any]:
    """Record a document, answering the record as it was written.

    `document_id` is the prefix the document is stored under, not an id minted here.

    `metadata` is what the journey collected, owners included: this service does not
    interpret it, because what an author is asked about a document is a question for
    the journey rather than for the store behind it.
    """
    record = {
        "_id": document_id,
        "metadata": metadata,
        "source": source,
        "createdAt": dt.datetime.now(tz=dt.UTC),
    }
    await database[DOCUMENTS].insert_one(record)
    return record


async def create_version(
    database: pymongo.asynchronous.database.AsyncDatabase,
    version_id: str,
    *,
    document_id: str,
    content_url: str,
    title: str | None = None,
    created_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one version of a document, answering the record as it was written.

    `version_id` is the prefix this version was stored under, and `content_url` names
    the file inside it - the whole of what is needed to read the version back.

    `title` is the document's own title as this version had it, kept so a person can
    see what a version was called without fetching it. See the module docstring for
    why a copy is the point here and would be a fault anywhere else.

    `created_by` is who was signed in, not who owns the document - see the module
    docstring. It is optional because the endpoint is not yet authenticated, and a
    version made by nobody is more honest than one attributed to a guess.
    """
    record = {
        "_id": version_id,
        DOCUMENT_ID: document_id,
        "contentUrl": content_url,
        "title": title,
        "createdBy": created_by,
        "createdAt": dt.datetime.now(tz=dt.UTC),
    }
    await database[VERSIONS].insert_one(record)
    return record
