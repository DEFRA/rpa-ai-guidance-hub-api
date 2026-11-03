"""The shapes a user takes over HTTP. Pydantic, validated on the way in, written into
the OpenAPI document, and spelt in camelCase on the wire; they convert to and from
`models`, and know nothing of how anything is stored.
"""

from __future__ import annotations

import datetime as dt

import pydantic

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


class User(pydantic.BaseModel):
    """A user as answered: the hub's own id for them, who they are to the identity
    provider, their name as of their latest sign-in, and when they first signed
    in."""

    id: ids.UserId
    identity: Identity
    display_name: str | None = pydantic.Field(alias="displayName")
    created_at: dt.datetime = pydantic.Field(alias="createdAt")

    @classmethod
    def from_model(cls, user: models.User) -> User:
        return cls(
            id=user.id,
            identity=Identity(tenantId=user.tenant_id, objectId=user.object_id),
            displayName=user.display_name,
            createdAt=user.created_at,
        )
