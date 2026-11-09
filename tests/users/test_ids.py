"""The hub's own ids for a user, and for each of their cases."""

from __future__ import annotations

import uuid

from app.users import ids


def test_a_new_user_id_is_a_time_ordered_uuid():
    user_id = ids.new_user_id()

    assert isinstance(user_id, uuid.UUID)
    assert user_id.version == 7


def test_a_new_case_key_is_a_random_uuid():
    """Random, not time-ordered: a key stands for a case in an address, so it says
    nothing of the case, nor of when it was made."""
    key = ids.new_case_key()

    assert isinstance(key, uuid.UUID)
    assert key.version == 4
