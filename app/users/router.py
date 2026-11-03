"""The people who use the hub, by the hub's own id for them.

`POST /users` is what the UI calls when someone signs in: it finds them by who they
are to the identity provider, making them on their first sign-in, and answers the
record whose id the UI then keeps for the session. Everything after that is by that
id. The API trusts the UI for who the user is, as it does for everything else.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated

import fastapi

from app.common import mongo
from app.guidance import records as guidance_records
from app.users import ids, json_patch, models, pins, records, schemas, service

if TYPE_CHECKING:
    import pymongo.asynchronous.database

router = fastapi.APIRouter(prefix="/users", tags=["users"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]


@router.post("", status_code=fastapi.status.HTTP_201_CREATED)
async def sign_in(
    signed_in: schemas.SignedIn, database: Database, response: fastapi.Response
) -> schemas.User:
    """Find the user who has signed in, or make them on their first sign-in.

    201 when this made them, 200 when they were there already; either way their
    name is brought up to date.
    """
    user, created = await records.find_or_create(
        database,
        tenant_id=signed_in.tenant_id,
        object_id=signed_in.object_id,
        display_name=signed_in.display_name,
        now=dt.datetime.now(tz=dt.UTC),
    )
    if not created:
        response.status_code = fastapi.status.HTTP_200_OK
    return await _answered(database, user)


@router.get("/{user_id}")
async def read_user(user_id: ids.UserId, database: Database) -> schemas.User:
    """One user, by the hub's id for them, with the guides they have pinned."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    return await _answered(database, user)


@router.patch("/{user_id}/pins", status_code=fastapi.status.HTTP_204_NO_CONTENT)
async def patch_pins(
    user_id: ids.UserId,
    operations: Annotated[list[schemas.PatchOperation], fastapi.Body()],
    database: Database,
) -> None:
    """Change the guides the user has pinned by a JSON Patch (sent as
    `application/json-patch+json`) on their ordered ids:
    `add` (at `/0` to pin at the top), `remove`, `move` and `test`, on top-level
    positions only. The patch applies whole or not at all.

    409 when a `test` fails, as for a page showing pins that have changed since;
    422 for anything else, a guide pinned twice, or a guide there is not.
    """
    try:
        added = pins.guides_added(operations)
    except pins.InvalidPinsError as invalid:
        raise _unprocessable(invalid) from invalid
    if await guidance_records.count_existing(database, list(added)) != len(added):
        missing = "A guide to pin does not exist"
        raise _unprocessable(missing)

    try:
        found = await records.patch_pins(
            database, user_id, lambda read: pins.applied(read, operations)
        )
    except (json_patch.PatchRefusedError, pins.InvalidPinsError) as refused:
        raise _unprocessable(refused) from refused
    except (json_patch.PatchTestFailedError, records.ListChangingError) as stale:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(stale)
        ) from stale
    if not found:
        raise _no_user(user_id)


async def _answered(
    database: pymongo.asynchronous.database.AsyncDatabase, user: models.User
) -> schemas.User:
    pinned = await service.pinned_titles(database, user)
    return schemas.User.from_model(user, pinned)


def _unprocessable(reason: object) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(reason)
    )


def _no_user(user_id: ids.UserId) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=f"No user {user_id}"
    )
