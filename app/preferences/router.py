"""A user's preferences: how they like the hub shown, the same wherever they sign in.

A service of its own, which knows a user only by the hub's id for them
(`app.users.ids`), as the UI holds it from their sign-in; it never reads the users
themselves. A user who has set no preferences has every one not set, and the hub
shows its defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import fastapi

from app.common import mongo
from app.preferences import records, schemas
from app.users import ids as users_ids

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/preferences", tags=["preferences"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]


@router.get("/{user_id}")
async def read_preferences(
    user_id: users_ids.UserId, database: Database
) -> schemas.Preferences:
    """Every one of the user's preferences, null for one they have not set."""
    return schemas.Preferences.from_model(await records.find(database, user_id))


@router.patch("/{user_id}")
async def patch_preferences(
    user_id: users_ids.UserId,
    patch: Annotated[
        schemas.Preferences, fastapi.Body(media_type="application/merge-patch+json")
    ],
    database: Database,
) -> schemas.Preferences:
    """Change the user's preferences by a JSON Merge Patch (sent as
    `application/merge-patch+json`) of the preferences alone: a value sets one, null
    takes it back to its default, and the rest stay as they are. Answers every
    preference, null for one not set.

    422 for a preference there is not, or a value it cannot take; then nothing
    changes.
    """
    chosen, cleared = patch.changes()
    kept = await records.merge(database, user_id, chosen, cleared)
    return schemas.Preferences.from_model(kept)
