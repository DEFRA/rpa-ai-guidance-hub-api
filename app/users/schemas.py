"""The shapes a user takes over HTTP. Pydantic, validated on the way in, written into
the OpenAPI document, and spelt in camelCase on the wire; they convert to and from
`models`, and know nothing of how anything is stored.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

import pydantic

from app.guidance import ids as guidance_ids
from app.guidance import models as guidance_models
from app.guidance import schemas as guidance_schemas
from app.users import ids, models


class Identity(pydantic.BaseModel):
    """Who signed in, as the identity provider names them: the tenant, and the
    user's object id within it. Unique together."""

    tenant_id: str = pydantic.Field(alias="tenantId", min_length=1)
    object_id: str = pydantic.Field(alias="objectId", min_length=1)

    model_config = pydantic.ConfigDict(populate_by_name=True)


class SignedIn(Identity):
    """Someone who has just signed in, as the UI knows them from their token."""

    display_name: str | None = pydantic.Field(default=None, alias="displayName")


class OpenedGuide(pydantic.BaseModel):
    """A guide the user has just opened."""

    guide_id: guidance_ids.DocumentId = pydantic.Field(alias="guideId")


class PinnedGuide(pydantic.BaseModel):
    """A guide the user has pinned, as the side navigation names it."""

    id: guidance_ids.DocumentId
    title: str | None


class User(pydantic.BaseModel):
    """A user as answered: the hub's own id for them, who they are to the identity
    provider, their name as of their latest sign-in, and when they first signed
    in. With them, the guides they have pinned, in their order, so a page asks
    once."""

    id: ids.UserId
    identity: Identity
    display_name: str | None = pydantic.Field(alias="displayName")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")
    pinned: list[PinnedGuide]

    @classmethod
    def from_model(
        cls,
        user: models.User,
        pinned: list[tuple[guidance_ids.DocumentId, str | None]],
    ) -> User:
        return cls(
            id=user.id,
            identity=Identity(tenantId=user.tenant_id, objectId=user.object_id),
            displayName=user.display_name,
            createdAt=user.created_at,
            pinned=[PinnedGuide(id=pin, title=title) for pin, title in pinned],
        )


class PinnedSummary(guidance_schemas.DocumentSummary):
    """A pinned guide as `GET /guides` lists it, with its position among all the
    user's pins, which a patch of the pins takes, whatever the filters show."""

    position: int

    @classmethod
    def pinned_at(
        cls, summary: guidance_models.DocumentSummary, position: int
    ) -> PinnedSummary:
        listed = guidance_schemas.DocumentSummary.from_model(summary)
        return cls(**listed.model_dump(by_alias=True), position=position)


class Pinned(pydantic.BaseModel):
    """The guides the user has pinned, in their order, as `GET /guides` lists them,
    and each filter's values among them with their counts."""

    items: list[PinnedSummary]
    counts: dict[str, list[guidance_schemas.OptionCount]]


class _PatchOperation(pydantic.BaseModel):
    """A JSON Patch operation (RFC 6902) on what its JSON Pointer `path` names."""

    path: str

    model_config = pydantic.ConfigDict(extra="forbid")


class AddOperation(_PatchOperation):
    """Put `value` at `path`: into a list at a position (`-` is the end), or as a
    member of an object."""

    op: Literal["add"]
    value: pydantic.JsonValue


class RemoveOperation(_PatchOperation):
    """Take out what `path` names."""

    op: Literal["remove"]


class MoveOperation(_PatchOperation):
    """Take out what `from` names, then add it at `path`, which is read in the
    document without it."""

    op: Literal["move"]
    from_: str = pydantic.Field(alias="from")


class TestOperation(_PatchOperation):
    """Check that `path` still holds `value`, so a patch made on what has changed
    since applies nothing."""

    op: Literal["test"]
    value: pydantic.JsonValue


PatchOperation = Annotated[
    AddOperation | RemoveOperation | MoveOperation | TestOperation,
    pydantic.Field(discriminator="op"),
]
