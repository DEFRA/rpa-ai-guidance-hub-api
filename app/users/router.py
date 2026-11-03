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
from app.users import ids, records, schemas

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
    return schemas.User.from_model(user)


@router.get("/{user_id}")
async def read_user(user_id: ids.UserId, database: Database) -> schemas.User:
    """One user, by the hub's id for them."""
    user = await records.find(database, user_id)
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No user {user_id}",
        )
    return schemas.User.from_model(user)
