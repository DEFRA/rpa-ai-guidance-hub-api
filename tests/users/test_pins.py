"""A user's pins changed by JSON Patch (RFC 6902), on their own: only `add`, `remove`,
`move` and `test`, on top-level positions, a `test` also on a pin's own fields,
applied whole or not at all."""

from __future__ import annotations

import uuid

import pydantic
import pytest

from app.users import json_patch, models, pins, schemas

A, B, C, D = (uuid.uuid7() for _ in range(4))

# Each guide's sections by anchor, as its latest version has them, in its order.
ONE, TWO = (uuid.uuid7() for _ in range(2))
SECTIONS = {A: models.GuideSections({"1-one": ONE, "2-two": TWO})}

PATCH = pydantic.TypeAdapter(list[schemas.PatchOperation])

# What refuses a patch: an operation that cannot apply to a list, or one that would
# make something other than pins.
REFUSED = (json_patch.PatchRefusedError, pins.InvalidPinsError)


def _pin(guide, section_id=None, heading=None):
    return models.Pin(document_id=guide, section_id=section_id, heading=heading)


def _guides(*guides):
    return [_pin(guide) for guide in guides]


def _patched(current, *operations):
    return pins.applied(
        list(current), PATCH.validate_python(list(operations)), SECTIONS
    )


def _add(value, at="/0"):
    return {"op": "add", "path": at, "value": value}


class TestAdding:
    def test_add_at_the_top_pins_first(self):
        assert _patched(_guides(A, B), _add({"guideId": str(C)})) == _guides(C, A, B)

    def test_add_at_the_end_pins_last(self):
        assert _patched(_guides(A), _add({"guideId": str(B)}, "/-")) == _guides(A, B)

    def test_a_section_added_by_its_anchor_is_kept_by_its_id_with_its_heading(self):
        added = {"guideId": str(A), "section": "2-two", "heading": "2 Two"}

        assert _patched(_guides(B), _add(added)) == [_pin(A, TWO, "2 Two"), _pin(B)]

    def test_a_section_added_by_its_id_is_kept_as_it_is(self):
        added = {"guideId": str(A), "sectionId": str(ONE), "heading": "1 One"}

        assert _patched([], _add(added)) == [_pin(A, ONE, "1 One")]

    def test_a_section_added_by_an_id_its_guide_does_not_have_is_refused(self):
        operation = _add({"guideId": str(A), "sectionId": str(uuid.uuid7())})

        with pytest.raises(REFUSED):
            _patched([], operation)

    def test_a_section_of_another_guide_added_by_its_id_is_refused(self):
        operation = _add({"guideId": str(B), "sectionId": str(ONE)})

        with pytest.raises(REFUSED):
            _patched([], operation)

    def test_a_pinned_section_its_guide_no_longer_has_stays(self):
        gone = uuid.uuid7()
        current = [_pin(A, gone, "Gone"), _pin(B)]

        assert _patched(current, {"op": "move", "from": "/1", "path": "/0"}) == [
            _pin(B),
            _pin(A, gone, "Gone"),
        ]

    def test_a_guide_and_a_section_of_it_can_both_be_pinned(self):
        added = {"guideId": str(A), "section": "1-one", "heading": "1 One"}

        assert _patched(_guides(A), _add(added)) == [_pin(A, ONE, "1 One"), _pin(A)]

    def test_a_section_the_guide_does_not_have_is_refused(self):
        operation = _add({"guideId": str(A), "section": "9-gone"})

        with pytest.raises(REFUSED):
            _patched([], operation)

    def test_a_section_of_a_guide_whose_sections_are_not_known_is_refused(self):
        operation = _add({"guideId": str(B), "section": "1-one"})

        with pytest.raises(REFUSED):
            _patched([], operation)

    def test_a_heading_without_a_section_is_refused(self):
        operation = _add({"guideId": str(C), "heading": "1 One"})

        with pytest.raises(REFUSED):
            _patched([], operation)

    def test_a_guide_already_pinned_is_refused(self):
        current = _guides(A, B)
        operation = _add({"guideId": str(A)})

        with pytest.raises(REFUSED):
            _patched(current, operation)

    def test_a_section_already_pinned_is_refused(self):
        current = [_pin(A, ONE, "1 One")]
        operation = _add({"guideId": str(A), "section": "1-one"})

        with pytest.raises(REFUSED):
            _patched(current, operation)

    @pytest.mark.parametrize(
        "value",
        [str(C), {"id": str(C)}, {"guideId": str(C), "title": "Extra"}],
        ids=["a bare guide id, as pins once were", "no guide id", "an extra field"],
    )
    def test_something_that_is_not_a_pin_is_refused(self, value):
        current = _guides(A, B)
        operation = _add(value)

        with pytest.raises(REFUSED):
            _patched(current, operation)


class TestRemoving:
    def test_remove_unpins_what_is_at_that_position(self):
        current = [_pin(A), _pin(A, ONE, "1 One"), _pin(B)]

        assert _patched(current, {"op": "remove", "path": "/1"}) == _guides(A, B)

    def test_a_position_past_the_end_is_refused(self):
        current = _guides(A, B)
        operation = {"op": "remove", "path": "/2"}

        with pytest.raises(REFUSED):
            _patched(current, operation)


class TestMoving:
    def test_moving_up_the_list(self):
        assert _patched(
            _guides(A, B, C, D), {"op": "move", "from": "/2", "path": "/0"}
        ) == _guides(C, A, B, D)

    def test_moving_down_the_list_inserts_into_the_list_without_the_pin(self):
        """RFC 6902: the pin is removed first, then put at `path` in what is left,
        so moving the first of four to /2 puts it third, not before the third."""
        assert _patched(
            _guides(A, B, C, D), {"op": "move", "from": "/0", "path": "/2"}
        ) == _guides(B, C, A, D)

    def test_moving_to_the_last_position(self):
        assert _patched(
            _guides(A, B, C), {"op": "move", "from": "/0", "path": "/2"}
        ) == _guides(B, C, A)

    def test_a_pinned_section_moves_with_its_heading(self):
        current = [_pin(B), _pin(A, TWO, "2 Two")]

        assert _patched(current, {"op": "move", "from": "/1", "path": "/0"}) == [
            _pin(A, TWO, "2 Two"),
            _pin(B),
        ]

    def test_a_move_past_the_end_is_refused(self):
        current = _guides(A, B, C)
        operation = {"op": "move", "from": "/0", "path": "/3"}

        with pytest.raises(REFUSED):
            _patched(current, operation)


class TestTesting:
    def test_a_test_of_a_pins_guide_and_section_that_holds_lets_the_rest_apply(self):
        current = [_pin(A), _pin(A, ONE, "1 One")]

        assert _patched(
            current,
            {"op": "test", "path": "/1/guideId", "value": str(A)},
            {"op": "test", "path": "/1/sectionId", "value": str(ONE)},
            {"op": "remove", "path": "/1"},
        ) == _guides(A)

    def test_a_whole_guide_is_pinned_with_no_section(self):
        assert _patched(
            [_pin(A, ONE, "1 One"), _pin(A)],
            {"op": "test", "path": "/1/sectionId", "value": None},
            {"op": "remove", "path": "/1"},
        ) == [_pin(A, ONE, "1 One")]

    def test_a_pinned_section_answers_its_anchor(self):
        assert _patched(
            [_pin(A, TWO, "2 Two")],
            {"op": "test", "path": "/0/section", "value": "2-two"},
        ) == [_pin(A, TWO, "2 Two")]

    def test_a_test_that_fails_applies_nothing(self):
        current = _guides(A, B)
        operations = PATCH.validate_python(
            [
                _add({"guideId": str(C)}),
                {"op": "test", "path": "/0/guideId", "value": str(A)},
            ]
        )

        with pytest.raises(json_patch.PatchTestFailedError):
            pins.applied(current, operations, SECTIONS)

        assert current == _guides(A, B)

    def test_a_test_of_a_section_where_the_whole_guide_is_fails(self):
        current = _guides(A)
        operation = {"op": "test", "path": "/0/sectionId", "value": str(ONE)}

        with pytest.raises(json_patch.PatchTestFailedError):
            _patched(current, operation)

    def test_a_test_past_the_end_fails_as_on_a_page_whose_pins_have_gone(self):
        current = _guides(A, B)
        operation = {"op": "test", "path": "/2/guideId", "value": str(C)}

        with pytest.raises(json_patch.PatchTestFailedError):
            _patched(current, operation)


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "replace", "path": "/0", "value": {"guideId": str(A)}},
            {"op": "copy", "from": "/0", "path": "/1"},
            {"op": "add", "path": "/0/heading", "value": "1 One"},
            {"op": "remove", "path": "/0/sectionId"},
            {"op": "move", "from": "/0/guideId", "path": "/1/guideId"},
            {"op": "test", "path": "/0/guideId/x", "value": str(A)},
            {"op": "add", "path": "/x", "value": {"guideId": str(A)}},
            {"op": "add", "path": "/01", "value": {"guideId": str(A)}},
            {"op": "add", "path": "0", "value": {"guideId": str(A)}},
            {"op": "remove", "path": "/-"},
        ],
    )
    def test_anything_but_add_remove_move_or_test_on_a_position(self, operation):
        def patch():
            return _patched(_guides(A, B), operation)

        with pytest.raises((*REFUSED, pydantic.ValidationError)):
            patch()
