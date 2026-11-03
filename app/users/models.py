"""A user as the service holds them, converting itself to and from the record Mongo
stores, so the names a record is stored under are written down once, here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping
from typing import Any

from app.guidance import ids as guidance_ids
from app.users import ids

# Who signed in, as the identity provider names them: the tenant and the user's
# object id within it. Nested only as stored.
IDENTITY = "identity"
TENANT_ID = f"{IDENTITY}.tenantId"
OBJECT_ID = f"{IDENTITY}.objectId"

# The guides the user has pinned, in their own order: the ids alone.
PINS = "pins"


@dataclasses.dataclass(frozen=True)
class User:
    """Someone who has signed in to the hub.

    The display name is a copy from their latest sign-in, so a page can name them
    without asking a directory. `pins` are in the user's own order.
    """

    id: ids.UserId
    tenant_id: str
    object_id: str
    display_name: str | None
    created_at: dt.datetime
    pins: tuple[guidance_ids.DocumentId, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            IDENTITY: {"tenantId": self.tenant_id, "objectId": self.object_id},
            "displayName": self.display_name,
            "createdAt": self.created_at,
            PINS: list(self.pins),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> User:
        identity = document[IDENTITY]
        return cls(
            id=document["_id"],
            tenant_id=identity["tenantId"],
            object_id=identity["objectId"],
            display_name=document.get("displayName"),
            created_at=document["createdAt"],
            pins=tuple(document.get(PINS, [])),
        )
