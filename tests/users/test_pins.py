"""A user's pins changed by JSON Patch (RFC 6902), on their own: only `add`, `remove`,
`move` and `test`, on top-level positions, applied whole or not at all."""

from __future__ import annotations

import uuid

import pydantic
import pytest

from app.users import json_patch, pins, schemas

A, B, C, D = (uuid.uuid7() for _ in range(4))

PATCH = pydantic.TypeAdapter(list[schemas.PatchOperation])

# What refuses a patch: an operation that cannot apply to a list, or one that would
# make something other than pins.
REFUSED = (json_patch.PatchRefusedError, pins.InvalidPinsError)


def _patched(current, *operations):
    return pins.applied(list(current), PATCH.validate_python(list(operations)))


class TestAdding:
    def test_add_at_the_top_pins_first(self):
        assert _patched([A, B], {"op": "add", "path": "/0", "value": str(C)}) == [
            C,
            A,
            B,
        ]

    def test_add_at_the_end_pins_last(self):
        assert _patched([A], {"op": "add", "path": "/-", "value": str(B)}) == [A, B]

    def test_a_guide_already_pinned_is_refused(self):
        operation = {"op": "add", "path": "/0", "value": str(A)}

        with pytest.raises(REFUSED):
            _patched([A, B], operation)

    def test_something_that_is_not_a_guide_id_is_refused(self):
        operation = {"op": "add", "path": "/0", "value": {"id": str(C)}}

        with pytest.raises(REFUSED):
            _patched([A, B], operation)


class TestRemoving:
    def test_remove_unpins_the_guide_at_that_position(self):
        assert _patched([A, B, C], {"op": "remove", "path": "/1"}) == [A, C]

    def test_a_position_past_the_end_is_refused(self):
        operation = {"op": "remove", "path": "/2"}

        with pytest.raises(REFUSED):
            _patched([A, B], operation)


class TestMoving:
    def test_moving_up_the_list(self):
        assert _patched([A, B, C, D], {"op": "move", "from": "/2", "path": "/0"}) == [
            C,
            A,
            B,
            D,
        ]

    def test_moving_down_the_list_inserts_into_the_list_without_the_guide(self):
        """RFC 6902: the guide is removed first, then put at `path` in what is left,
        so moving the first of four to /2 puts it third, not before the third."""
        assert _patched([A, B, C, D], {"op": "move", "from": "/0", "path": "/2"}) == [
            B,
            C,
            A,
            D,
        ]

    def test_moving_to_the_last_position(self):
        assert _patched([A, B, C], {"op": "move", "from": "/0", "path": "/2"}) == [
            B,
            C,
            A,
        ]

    def test_a_move_past_the_end_is_refused(self):
        operation = {"op": "move", "from": "/0", "path": "/3"}

        with pytest.raises(REFUSED):
            _patched([A, B, C], operation)


class TestTesting:
    def test_a_test_that_holds_lets_the_rest_apply(self):
        assert _patched(
            [A, B],
            {"op": "test", "path": "/1", "value": str(B)},
            {"op": "remove", "path": "/1"},
        ) == [A]

    def test_a_test_that_fails_applies_nothing(self):
        current = [A, B]
        operations = PATCH.validate_python(
            [
                {"op": "add", "path": "/0", "value": str(C)},
                {"op": "test", "path": "/0", "value": str(A)},
            ]
        )

        with pytest.raises(json_patch.PatchTestFailedError):
            pins.applied(current, operations)

        assert current == [A, B]

    def test_a_test_past_the_end_fails_as_on_a_page_whose_pins_have_gone(self):
        operation = {"op": "test", "path": "/2", "value": str(C)}

        with pytest.raises(json_patch.PatchTestFailedError):
            _patched([A, B], operation)


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "replace", "path": "/0", "value": str(A)},
            {"op": "copy", "from": "/0", "path": "/1"},
            {"op": "add", "path": "/0/title", "value": str(A)},
            {"op": "add", "path": "/x", "value": str(A)},
            {"op": "add", "path": "/01", "value": str(A)},
            {"op": "add", "path": "0", "value": str(A)},
            {"op": "remove", "path": "/-"},
        ],
    )
    def test_anything_but_add_remove_move_or_test_on_a_position(self, operation):
        def patch():
            return _patched([A, B], operation)

        with pytest.raises((*REFUSED, pydantic.ValidationError)):
            patch()
