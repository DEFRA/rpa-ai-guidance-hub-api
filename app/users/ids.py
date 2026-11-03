"""The hub's own id for a user, minted at their first sign-in.

A type of its own, as `app.guidance.ids` has for documents and versions, so the type
checker refuses one where another belongs. A uuid, version 7, stored in Mongo as a
native UUID. Everything that refers to a user uses this id, never the identity
provider's: a re-created account, or a move between tenants, then changes one field
of one record rather than every reference to the user.
"""

from __future__ import annotations

import uuid
from typing import NewType

UserId = NewType("UserId", uuid.UUID)


def new_user_id() -> UserId:
    return UserId(uuid.uuid7())
