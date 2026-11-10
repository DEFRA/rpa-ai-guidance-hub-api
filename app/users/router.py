"""The people who use the hub, by the hub's own id for them.

`POST /users` is what the UI calls when someone signs in: it finds them by who they
are to the identity provider, making them on their first sign-in, and answers the
record whose id the UI then keeps for the session. Everything after that is by that
id. The API trusts the UI for who the user is, as it does for everything else.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated, Any

import fastapi

from app import config
from app.common import mongo, s3
from app.guidance import records as guidance_records
from app.guidance import schemas as guidance_schemas
from app.guidance import service as guidance_service
from app.users import cases, ids, json_patch, models, pins, records, schemas, service

if TYPE_CHECKING:
    import pymongo.asynchronous.database

    from app.guidance import ids as guidance_ids

router = fastapi.APIRouter(prefix="/users", tags=["users"])

Database = Annotated[
    "pymongo.asynchronous.database.AsyncDatabase", fastapi.Depends(mongo.get_db)
]


def _hub_filters(
    scheme: Annotated[list[str] | None, fastapi.Query()] = None,
    system: Annotated[list[str] | None, fastapi.Query()] = None,
    audience: Annotated[list[str] | None, fastapi.Query()] = None,
    no_scheme: Annotated[
        bool, fastapi.Query(description="The guides specific to no scheme too")
    ] = False,
) -> dict[str, list[str | None]]:
    """The hub's filters, as `GET /guides` takes them."""
    return guidance_service.chosen_filters(
        {"scheme": scheme, "system": system, "audience": audience},
        no_scheme=no_scheme,
    )


HubFilters = Annotated[dict[str, list[str | None]], fastapi.Depends(_hub_filters)]

S3Client = Annotated[Any, fastapi.Depends(s3.get_s3_client)]


def sbi_bookmarks() -> bool:
    """Whether a guide can be bookmarked to an SBI here, as well as to a Case ID:
    not unless the environment's setting says so."""
    return config.get_config().feature_sbi_bookmarks


SbiBookmarks = Annotated[bool, fastapi.Depends(sbi_bookmarks)]


@router.post("", status_code=fastapi.status.HTTP_201_CREATED)
async def sign_in(
    signed_in: schemas.SignedIn,
    database: Database,
    s3_client: S3Client,
    response: fastapi.Response,
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
    return await _answered(database, user, s3_client)


@router.get("/{user_id}")
async def read_user(
    user_id: ids.UserId, database: Database, s3_client: S3Client
) -> schemas.User:
    """One user, by the hub's id for them, with the guides and sections they have
    pinned and the guides bookmarked to their cases."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    return await _answered(database, user, s3_client)


@router.patch("/{user_id}/pins", status_code=fastapi.status.HTTP_204_NO_CONTENT)
async def patch_pins(
    user_id: ids.UserId,
    operations: Annotated[list[schemas.PatchOperation], fastapi.Body()],
    database: Database,
    s3_client: S3Client,
) -> None:
    """Change the guides, and sections of guides, the user has pinned by a JSON
    Patch (sent as `application/json-patch+json`) on the ordered list of them, each
    `{guideId, sectionId, section, heading}` with its section at its anchor, a whole
    guide with no section: `add` (at `/0` to pin at the top), `remove` and `move` on
    top-level positions only, and `test` on those or a pin's own fields. A section is
    kept by its id; one added by its anchor alone (`section`) is the section the
    guide's latest version has there. The patch applies whole or not at all.

    Positions are as the user's pins are answered, without those of guides that no
    longer exist, which the patch leaves out of the pins it writes.

    409 when a `test` fails, as for a page showing pins that have changed since;
    422 for anything else: a guide or a section pinned twice, a guide there is not,
    or a section it has not.
    """
    try:
        added = pins.guides_added(operations)
    except pins.InvalidPinsError as invalid:
        raise _unprocessable(invalid) from invalid
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    named = {pin.document_id for pin in user.pins}
    existing_guides = await guidance_records.existing(database, [*named, *added])
    if not added <= existing_guides:
        missing = "A guide to pin does not exist"
        raise _unprocessable(missing)
    gone = named - existing_guides

    async def patched(read: list[models.Pin]) -> list[models.Pin]:
        live = [pin for pin in read if pin.document_id not in gone]
        needed = pins.guides_with_sections(live, operations)
        sections = await service.guide_sections(database, needed, s3_client)
        return pins.applied(live, operations, sections)

    try:
        found = await records.patch_pins(database, user_id, patched)
    except (json_patch.PatchRefusedError, pins.InvalidPinsError) as refused:
        raise _unprocessable(refused) from refused
    except (json_patch.PatchTestFailedError, records.ListChangingError) as stale:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(stale)
        ) from stale
    if not found:
        raise _no_user(user_id)


@router.patch("/{user_id}/cases", status_code=fastapi.status.HTTP_204_NO_CONTENT)
async def patch_cases(
    user_id: ids.UserId,
    operations: Annotated[list[schemas.PatchOperation], fastapi.Body()],
    database: Database,
    s3_client: S3Client,
    sbis_allowed: SbiBookmarks,
) -> None:
    """Change the user's cases and the sections of guides bookmarked to them by a
    JSON Patch (sent as `application/json-patch+json`) over the whole list of cases,
    each `{key, type, ref, bookmarks}`, each bookmark `{guideId, sectionId, section,
    heading, createdAt}` with its section at its anchor: `add`, `remove`, `move` and
    `test`, at any depth. A section is kept by its id; one added by its anchor alone
    (`section`) is the section the guide's latest version has there. A new case is
    given its key. The patch applies whole or not at all; a case left with no
    bookmarks stays.

    409 when a `test` fails, as for a page showing cases that have changed since;
    422 for anything else: an operation that cannot apply, a reference that is not
    one (a Case ID is a letter then seven digits, an SBI nine digits from 105000000),
    an SBI where SBIs are switched off, a case or a key twice, a section bookmarked
    twice to one case, a guide there is not, or a section it has not.
    """
    now = dt.datetime.now(tz=dt.UTC)

    async def sections_of(
        guides: set[guidance_ids.DocumentId],
    ) -> dict[guidance_ids.DocumentId, models.GuideSections]:
        return await service.guide_sections(database, guides, s3_client)

    async def patched(read: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await cases.applied(
            read, operations, now, sections_of, sbis_allowed=sbis_allowed
        )

    try:
        found = await records.patch_cases(database, user_id, patched)
    except (json_patch.PatchRefusedError, cases.InvalidCasesError) as refused:
        raise _unprocessable(refused) from refused
    except (json_patch.PatchTestFailedError, records.ListChangingError) as stale:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(stale)
        ) from stale
    if not found:
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
    filters: HubFilters,
    s3_client: S3Client,
) -> schemas.Pinned:
    """The user's pins, guides and sections of guides, in their order, each with its
    guide as `GET /guides` lists it and its position among all the user's pins, and
    a section with its id, anchor and heading. The hub's filters narrow the list by
    each pin's guide, and the counts are among the guides pinned, each counted once.
    A guide that no longer exists is left out."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    try:
        listed, counts = await service.pinned(database, user, filters, s3_client)
    except guidance_service.UnknownFilterValueError as unknown:
        raise _unprocessable(unknown) from unknown
    return schemas.Pinned(
        items=[schemas.PinnedSummary.from_listed(pin) for pin in listed],
        counts=guidance_schemas.counts_from_model(counts),
    )


@router.get("/{user_id}/recently-opened")
async def read_recently_opened(
    user_id: ids.UserId, database: Database, filters: HubFilters
) -> schemas.RecentlyOpened:
    """The guides the user opened most recently, newest first, as `GET /guides`
    lists them, each with when it was opened, with the same filters and counts. A
    guide that no longer exists is left out."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    try:
        listed, counts = await service.recently_opened(database, user, filters)
    except guidance_service.UnknownFilterValueError as unknown:
        raise _unprocessable(unknown) from unknown
    return schemas.RecentlyOpened(
        items=[
            schemas.RecentlyOpenedSummary.opened(summary, opened_at)
            for summary, opened_at in listed
        ],
        counts=guidance_schemas.counts_from_model(counts),
    )


@router.get("/{user_id}/bookmarked-guides")
async def read_bookmarked_guides(
    user_id: ids.UserId,
    database: Database,
    filters: HubFilters,
    case_key: Annotated[
        ids.CaseKey | None,
        fastapi.Query(
            alias="case", description="Only the guides of the case with this key"
        ),
    ] = None,
) -> schemas.BookmarkedGuides:
    """The guides bookmarked to the user's cases, each once, in the order of the
    cases and, in each, the order the user put them, as `GET /guides` lists them,
    with the same filters and counts; with `case`, those of that case alone. 404 for
    a case the user has not got. A guide that no longer exists is left out."""
    user = await records.find(database, user_id)
    if user is None:
        raise _no_user(user_id)
    cases = [case for case in user.cases if case_key in (None, case.key)]
    if case_key is not None and not cases:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No case {case_key} for user {user_id}",
        )
    try:
        listed, counts = await service.bookmarked(database, cases, filters)
    except guidance_service.UnknownFilterValueError as unknown:
        raise _unprocessable(unknown) from unknown
    return schemas.BookmarkedGuides(
        items=[
            guidance_schemas.DocumentSummary.from_model(summary) for summary in listed
        ],
        counts=guidance_schemas.counts_from_model(counts),
    )


async def _answered(
    database: pymongo.asynchronous.database.AsyncDatabase,
    user: models.User,
    s3_client: Any,
) -> schemas.User:
    guides = await service.guides_in_brief(database, user, s3_client)
    return schemas.User.from_model(user, guides)


def _unprocessable(reason: object) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(reason)
    )


def _no_user(user_id: ids.UserId) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=f"No user {user_id}"
    )
