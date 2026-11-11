"""The `preferences` collection: one record for each user who has set a preference,
keyed by the hub's id for them (`_id`), with each preference they have set by the
name the API gives it (`schemas.Preferences`). A preference not set has no field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
