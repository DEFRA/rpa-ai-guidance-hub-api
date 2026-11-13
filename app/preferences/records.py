"""The `preferences` collection: one record for each user who has set a preference,
keyed by the hub's id for them (`_id`), with each preference they have set by the
name the API gives it (`schemas.Preferences`). A preference not set has no field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pymongo

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import pymongo.asynchronous.database

    from app.users import ids as users_ids

PREFERENCES = "preferences"


async def find(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: users_ids.UserId,
) -> dict[str, Any]:
    """The preferences the user has set, by name: none if they have set none."""
    record = await database[PREFERENCES].find_one({"_id": user_id}, {"_id": False})
    return record or {}


async def merge(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user_id: users_ids.UserId,
    chosen: Mapping[str, Any],
    cleared: Iterable[str],
) -> dict[str, Any]:
    """Set the preferences in `chosen` and take those named in `cleared` back to
    their defaults, in one update, leaving the others as they are. The user's first
    change makes their record.

    Returns the preferences the user has set, after it.
    """
    change: dict[str, Any] = {}
    if chosen:
        change["$set"] = dict(chosen)
    if cleared:
        change["$unset"] = dict.fromkeys(cleared, "")
    if not change:
        return await find(database, user_id)

    record = await database[PREFERENCES].find_one_and_update(
        {"_id": user_id},
        change,
        projection={"_id": False},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return record or {}
