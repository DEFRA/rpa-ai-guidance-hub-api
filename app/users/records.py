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
import typing
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import pymongo
import pymongo.errors

from app.guidance import ids as guidance_ids
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


async def convert_pins_kept_as_ids(
    database: pymongo.asynchronous.database.AsyncDatabase,
) -> None:
    """Turn each pin kept as a bare guide id, as every pin was before a section could
    be pinned, into a pin of that whole guide, where it was. Idempotent, and called
    at startup, before a request reads a pin; not needed once every environment has
    run it."""
    # Each pin, as the `$map` below names it.
    each_pin = "$$pin"
    # A whole guide's pin as stored, with the guide id `$map` gives it.
    of_guide_id = models.Pin(
        document_id=typing.cast("guidance_ids.DocumentId", each_pin)
    ).to_document()
    await database[USERS].update_many(
        {models.PINS: {"$type": "binData"}},
        [
            {
                "$set": {
                    models.PINS: {
                        "$map": {
                            "input": f"${models.PINS}",
                            "as": "pin",
                            "in": {
                                "$cond": [
                                    {"$eq": [{"$type": each_pin}, "binData"]},
                                    of_guide_id,
                                    each_pin,
                                ]
                            },
                        }
                    }
                }
            }
        ],
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
    now: dt.datetime,
) -> bool:
    """Add that the user opened the guide, and when, to their record.

    Each opening is only added: the list is what `models.User` makes of the openings
    when it reads them, by their times, so the order they arrive in does not matter.
    Openings no longer on that list then go, so the record stays small.

    Returns False if there is no such user.
    """
    opened = models.Opened(document_id=document_id, opened_at=now).to_document()
    found = await database[USERS].find_one_and_update(
        {"_id": user_id},
        {"$push": {models.RECENTLY_OPENED: opened}},
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if found is None:
        return False
    listed = models.User.from_document(found).recently_opened
    surplus = [
        entry
        for entry in found[models.RECENTLY_OPENED]
        if models.Opened.from_document(entry) not in listed
    ]
    if surplus:
        await database[USERS].update_one(
            {"_id": user_id}, {"$pull": {models.RECENTLY_OPENED: {"$in": surplus}}}
        )
    return True


# How many times a patch is tried again when the list changes under it.
_PATCH_ATTEMPTS = 5


class ListChangingError(RuntimeError):
    """Raised when a user's list keeps changing under a patch, too often to apply
    it."""


async def patch_pins(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    patched: Callable[[list[models.Pin]], Awaitable[list[models.Pin]]],
) -> bool:
    """Change the user's pins to what `patched` makes of them, whole or not at all
    (`_patch_list`). `patched` may read the database, for the sections the pins
    name."""

    async def made(read: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pins = await patched([models.Pin.from_document(pin) for pin in read])
        return [pin.to_document() for pin in pins]

    return await _patch_list(database, user_id, models.PINS, made)


async def patch_cases(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    patched: Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]],
) -> bool:
    """Change the user's cases, as stored, to what `patched` makes of them, whole or
    not at all (`_patch_list`). `patched` may read the database, for what the cases
    name."""
    return await _patch_list(database, user_id, models.CASES, patched)


async def _patch_list[T](
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: ids.UserId,
    field: str,
    patched: Callable[[list[T]], Awaitable[list[T]]],
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
            {"$set": {field: await patched(read)}},
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
