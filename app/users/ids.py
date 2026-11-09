"""The hub's own id for a user, minted at their first sign-in, and the key of each of
their cases.

Types of their own, as `app.guidance.ids` has for documents and versions, so the type
checker refuses one where another belongs, stored in Mongo as native UUIDs.

A user id is a uuid, version 7. Everything that refers to a user uses this id, never
the identity provider's: a re-created account, or a move between tenants, then changes
one field of one record rather than every reference to the user.

A case key is a uuid, version 4: random. It names one of the user's cases wherever a
case ID or SBI must not go, such as an address, which analytics and the logs record.
So it says nothing of the case, nor of when it was made.
"""

from __future__ import annotations

import uuid
from typing import NewType

UserId = NewType("UserId", uuid.UUID)
CaseKey = NewType("CaseKey", uuid.UUID)


def new_user_id() -> UserId:
    return UserId(uuid.uuid7())


def new_case_key() -> CaseKey:
    return CaseKey(uuid.uuid4())
