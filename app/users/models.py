"""A user as the service holds them, converting itself to and from the record Mongo
stores, so the names a record is stored under are written down once, here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.users import ids

# Who signed in, as the identity provider names them: the tenant and the user's
# object id within it. Nested only as stored.
IDENTITY = "identity"
TENANT_ID = f"{IDENTITY}.tenantId"
OBJECT_ID = f"{IDENTITY}.objectId"


@dataclass(frozen=True)
class User:
    """Someone who has signed in to the hub.

    The display name is a copy from their latest sign-in, so a page can name them
    without asking a directory.
    """

    id: ids.UserId
    tenant_id: str
    object_id: str
    display_name: str | None
    created_at: dt.datetime

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            IDENTITY: {"tenantId": self.tenant_id, "objectId": self.object_id},
            "displayName": self.display_name,
            "createdAt": self.created_at,
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
        )
