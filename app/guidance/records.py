"""The record of a guide: its metadata, and where its content was stored.

The database holds what a guide *is* to the people using it - the title an author
typed, who it is for, what it is meant to achieve - and a pointer to where the
converted Markdown went. The Markdown itself stays in the object store, because a
document is bytes and a database is an index: everything here is something a guide
is found or filtered by.

`uploadId` is unique, and that is the whole of how a guide is converted once. The
journey that produces one can be submitted twice - a refresh, a back button, a
retried request - and each of those means the same guide rather than another one.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pymongo.asynchronous.database

COLLECTION = "documents"

# The upload a guide was converted from, and the only thing that says two requests
# mean the same guide.
_UPLOAD_ID = "source.uploadId"


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """Make the index the uniqueness of a guide rests on.

    Idempotent, and called at startup rather than on the first write: an index that
    only appears once something has been written is an index that was not there for
    whatever raced it.
    """
    await database[COLLECTION].create_index(_UPLOAD_ID, unique=True)


async def find_by_upload(
    database: pymongo.asynchronous.database.AsyncDatabase, upload_id: str
) -> dict[str, Any] | None:
    """The guide already converted from `upload_id`, if there is one."""
    found: dict[str, Any] | None = await database[COLLECTION].find_one(
        {_UPLOAD_ID: upload_id}
    )
    return found


async def find(
    database: pymongo.asynchronous.database.AsyncDatabase, document_id: str
) -> dict[str, Any] | None:
    """One guide by its id."""
    found: dict[str, Any] | None = await database[COLLECTION].find_one(
        {"_id": document_id}
    )
    return found


async def create(
    database: pymongo.asynchronous.database.AsyncDatabase,
    document_id: str,
    *,
    metadata: dict[str, Any],
    source: dict[str, Any],
    content: dict[str, Any],
    created_by: str | None = None,
) -> dict[str, Any]:
    """Record a converted guide, answering the record as it was written."""
    record = {
        "_id": document_id,
        "metadata": metadata,
        "source": source,
        "content": content,
        "createdBy": created_by,
        "createdAt": dt.datetime.now(tz=dt.UTC),
    }
    await database[COLLECTION].insert_one(record)
    return record
