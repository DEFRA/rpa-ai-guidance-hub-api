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
from typing import TYPE_CHECKING, Any

import pymongo
import pymongo.errors

from app.users import ids, models

if TYPE_CHECKING:
    import pymongo.asynchronous.database

USERS = "users"


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
