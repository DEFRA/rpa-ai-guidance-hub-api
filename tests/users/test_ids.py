"""The hub's own id for a user."""

from __future__ import annotations

import uuid

from app.users import ids


def test_a_new_user_id_is_a_time_ordered_uuid():
    user_id = ids.new_user_id()

    assert isinstance(user_id, uuid.UUID)
    assert user_id.version == 7
