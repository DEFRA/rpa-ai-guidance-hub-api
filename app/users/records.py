"""The `users` collection: one record per person who has signed in.

A user is found by who signed in - the identity provider's tenant and object id,
unique together - only when they sign in. After that the UI holds the hub's own id
(`ids`), and every lookup is by `_id`.

**Signing in is one find-or-create on the identity.** An upsert, keyed on the
identity and backed by its unique index, so two first sign-ins at once end with one
record: the one that loses the race to insert finds the winner's. The display name is
refreshed each time; the id and `createdAt` are set only when the record is made.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pymongo
import pymongo.errors

from app.guidance import ids as guidance_ids
from app.users import ids, models

if TYPE_CHECKING:
    import pymongo.asynchronous.database

USERS = "users"

# How many guides a user's recently opened list keeps.
RECENTLY_OPENED_KEPT = 5


class VanishedUserError(LookupError):
    """Raised when a user another sign-in made is gone by the time this one looks."""


async def ensure_indexes(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """Make the unique index on the identity. Idempotent, and called at startup, so
    that the first two sign-ins race an index that already exists."""
    await database[USERS].create_index(
        [(models.TENANT_ID, 1), (models.OBJECT_ID, 1)], unique=True
    )


async def find_or_create(
    database: pymongo.asynchronous.database.AsyncDatabase,
    *,
    tenant_id: str,
    object_id: str,
    display_name: str | None,
    now: dt.datetime,
) -> tuple[models.User, bool]:
    """The user who signed in, made if this is their first sign-in.

    Returns the user, and whether this call made them.
    """
    minted = ids.new_user_id()
    identity = {models.TENANT_ID: tenant_id, models.OBJECT_ID: object_id}
    update: dict[str, Any] = {
        "$set": {"displayName": display_name},
        "$setOnInsert": {"_id": minted, "createdAt": now},
    }

    try:
        record = await database[USERS].find_one_and_update(
            identity,
            update,
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER,
        )
    except pymongo.errors.DuplicateKeyError:
        # Another first sign-in made the record between this one's look and its
        # insert. MongoDB 4.2 and later retry such an upsert themselves; this is
        # for a server that does not. The record is there now, so this one finds it.
        record = await database[USERS].find_one_and_update(
            identity,
            {"$set": update["$set"]},
            return_document=pymongo.ReturnDocument.AFTER,
        )

    if record is None:
        message = f"No user {tenant_id}/{object_id}, though another sign-in made one"
        raise VanishedUserError(message)

    user = models.User.from_document(record)
    return user, user.id == minted


async def find(
    database: pymongo.asynchronous.database.AsyncDatabase, user_id: ids.UserId
) -> models.User | None:
    """One user by the hub's id."""
    found = await database[USERS].find_one({"_id": user_id})
    return None if found is None else models.User.from_document(found)


async def record_opened(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    document_id: guidance_ids.DocumentId,
) -> bool:
    """Put the guide first in the user's recently opened list, with when.

    One atomic update of the user's record, so guides opened at once cannot lose
    each other or appear twice: the guide's earlier entry, if any, goes, the guide
    goes first, and only the newest `RECENTLY_OPENED_KEPT` stay. The time is the
    database's as it writes, so a request that reaches it late still goes first
    as the newest, and the list stays newest first.

    Returns False if there is no such user.
    """
    entries = f"${models.RECENTLY_OPENED}"
    kept_others = {
        "$filter": {
            "input": {"$ifNull": [entries, []]},
            "cond": {"$ne": [f"$$this.{models.OPENED_GUIDE}", document_id]},
        }
    }
    opened = {
        models.OPENED_GUIDE: {"$literal": document_id},
        models.OPENED_AT: "$$NOW",
    }
    result = await database[USERS].update_one(
        {"_id": user_id},
        [
            {
                "$set": {
                    models.RECENTLY_OPENED: {
                        "$slice": [
                            {"$concatArrays": [[opened], kept_others]},
                            RECENTLY_OPENED_KEPT,
                        ]
                    }
                }
            }
        ],
    )
    return result.matched_count == 1


# How many times a patch is tried again when the list changes under it.
_PATCH_ATTEMPTS = 5


class ListChangingError(RuntimeError):
    """Raised when a user's list keeps changing under a patch, too often to apply
    it."""


async def patch_pins(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    patched: Callable[[list[guidance_ids.DocumentId]], list[guidance_ids.DocumentId]],
) -> bool:
    """Change the user's pins to what `patched` makes of them, whole or not at all
    (`_patch_list`)."""
    return await _patch_list(database, user_id, models.PINS, patched)


async def _patch_list[T](
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    field: str,
    patched: Callable[[list[T]], list[T]],
) -> bool:
    """Change one of the user's lists to what `patched` makes of it, whole or not at
    all.

    The list is read as stored, `patched` makes the new one, and it is written only
    if the record still holds the list that was read; if another change came
    between, it is all done again on the list as it now is. So two patches at once
    both apply, each to the other's result, and neither is lost.

    Returns False if there is no such user. Whatever `patched` raises is raised.

    Raises:
        ListChangingError: if the list changed under every attempt.
    """
    for _ in range(_PATCH_ATTEMPTS):
        read: list[T] | None = await _list_of(database, user_id, field)
        if read is None:
            return False

        written = await database[USERS].update_one(
            {"_id": user_id, **_still(field, read)},
            {"$set": {field: patched(read)}},
        )
        if written.matched_count == 1:
            return True

    message = f"The {field} of user {user_id} kept changing"
    raise ListChangingError(message)


async def _list_of(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    field: str,
) -> list[Any] | None:
    """One of the user's lists as stored, empty if they have never had one; None if
    there is no such user."""
    record = await database[USERS].find_one({"_id": user_id}, {field: True})
    return None if record is None else list(record.get(field, []))


def _still(field: str, read: list[Any]) -> dict[str, Any]:
    """The condition that the record's list is still `read`: in order, or, for
    none, a record that has never had one."""
    if read:
        return {field: read}
    return {"$or": [{field: {"$exists": False}}, {field: []}]}
