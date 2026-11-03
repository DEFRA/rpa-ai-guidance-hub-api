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
from app.guidance import schemas as guidance_schemas
from app.guidance import service as guidance_service
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

    Positions are as the user's pins are answered, without the guides that no
    longer exist, which the patch leaves out of the pins it writes.

    409 when a `test` fails, as for a page showing pins that have changed since;
    422 for anything else, a guide pinned twice, or a guide there is not.
    """
    try:
        added = pins.guides_added(operations)
    except pins.InvalidPinsError as invalid:
        raise _unprocessable(invalid) from invalid
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    existing_guides = await guidance_records.existing(database, [*user.pins, *added])
    if not added <= existing_guides:
        missing = "A guide to pin does not exist"
        raise _unprocessable(missing)
    gone = set(user.pins) - existing_guides

    try:
        patched = await records.patch_pins(
            database,
            user_id,
            lambda read: pins.applied(
                [pin for pin in read if pin not in gone], operations
            ),
        )
    except (json_patch.PatchRefusedError, pins.InvalidPinsError) as refused:
        raise _unprocessable(refused) from refused
    except (json_patch.PatchTestFailedError, records.ListChangingError) as stale:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(stale)
        ) from stale
    if not patched:
        raise _no_user(user_id)


@router.post(
    "/{user_id}/recently-opened", status_code=fastapi.status.HTTP_204_NO_CONTENT
)
async def record_opened(
    user_id: ids.UserId, opened: schemas.OpenedGuide, database: Database
) -> None:
    """Record that the user has opened a guide: it goes first in their recently
    opened guides, which keep the newest few, each guide once.

    422 for a guide there is not.
    """
    if not await guidance_records.existing(database, [opened.guide_id]):
        missing = "The guide opened does not exist"
        raise _unprocessable(missing)
    recorded = await records.record_opened(
        database, user_id, opened.guide_id, dt.datetime.now(tz=dt.UTC)
    )
    if not recorded:
        raise _no_user(user_id)


@router.get("/{user_id}/pins")
async def read_pins(
    user_id: ids.UserId,
    database: Database,
    scheme: Annotated[list[str] | None, fastapi.Query()] = None,
    system: Annotated[list[str] | None, fastapi.Query()] = None,
    audience: Annotated[list[str] | None, fastapi.Query()] = None,
    no_scheme: Annotated[
        bool, fastapi.Query(description="The guides specific to no scheme too")
    ] = False,
) -> schemas.Pinned:
    """The guides the user has pinned, in their order, as `GET /guides` lists them,
    with the same filters and counts. A guide that no longer exists is left out."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    filters = guidance_service.chosen_filters(
        {"scheme": scheme, "system": system, "audience": audience},
        no_scheme=no_scheme,
    )
    try:
        listed, counts = await service.pinned(database, user, filters)
    except guidance_service.UnknownFilterValueError as unknown:
        raise _unprocessable(unknown) from unknown
    return schemas.Pinned(
        items=[
            schemas.PinnedSummary.pinned_at(summary, position)
            for summary, position in listed
        ],
        counts=guidance_schemas.counts_from_model(counts),
    )


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
