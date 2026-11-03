"""JSON Patch (RFC 6902) on a document in memory: `add`, `remove`, `move` and `test`,
at any depth, applied whole or not at all."""

from __future__ import annotations

import pydantic
import pytest

from app.users import json_patch, schemas

PATCH = pydantic.TypeAdapter(list[schemas.PatchOperation])


def _patched(document, *operations):
    return json_patch.applied(document, PATCH.validate_python(list(operations)))


def _cases():
    return [
        {"ref": "W1", "bookmarks": [{"guideId": "a"}, {"guideId": "b"}]},
        {"ref": "W2", "bookmarks": []},
    ]


class TestAdding:
    def test_add_inserts_into_a_list_at_any_depth(self):
        patched = _patched(
            _cases(), {"op": "add", "path": "/0/bookmarks/1", "value": {"guideId": "c"}}
        )

        assert patched[0]["bookmarks"] == [
            {"guideId": "a"},
            {"guideId": "c"},
            {"guideId": "b"},
        ]

    def test_add_at_the_end_of_a_list(self):
        patched = _patched(
            _cases(), {"op": "add", "path": "/1/bookmarks/-", "value": {"guideId": "c"}}
        )

        assert patched[1]["bookmarks"] == [{"guideId": "c"}]

    def test_add_sets_a_member_of_an_object(self):
        patched = _patched(_cases(), {"op": "add", "path": "/1/ref", "value": "W3"})

        assert patched[1]["ref"] == "W3"

    def test_a_pointer_escapes_its_slashes_and_tildes(self):
        patched = _patched({}, {"op": "add", "path": "/a~1b~0c", "value": 1})

        assert patched == {"a/b~c": 1}


class TestRemoving:
    def test_remove_takes_out_what_the_path_names(self):
        patched = _patched(_cases(), {"op": "remove", "path": "/0/bookmarks/0"})

        assert patched[0]["bookmarks"] == [{"guideId": "b"}]

    def test_removing_the_last_of_a_list_leaves_the_list(self):
        patched = _patched(_cases(), {"op": "remove", "path": "/1"})

        assert [case["ref"] for case in patched] == ["W1"]


class TestMoving:
    def test_moving_down_inserts_into_the_list_without_it(self):
        patched = _patched(
            [{"n": 0}, {"n": 1}, {"n": 2}], {"op": "move", "from": "/0", "path": "/1"}
        )

        assert patched == [{"n": 1}, {"n": 0}, {"n": 2}]

    def test_moving_from_one_list_to_another(self):
        patched = _patched(
            _cases(),
            {"op": "move", "from": "/0/bookmarks/1", "path": "/1/bookmarks/0"},
        )

        assert patched[0]["bookmarks"] == [{"guideId": "a"}]
        assert patched[1]["bookmarks"] == [{"guideId": "b"}]


class TestTesting:
    def test_a_test_that_holds_changes_nothing(self):
        operation = {"op": "test", "path": "/0/bookmarks/1", "value": {"guideId": "b"}}

        assert _patched(_cases(), operation) == _cases()

    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "test", "path": "/0/ref", "value": "W2"},
            {"op": "test", "path": "/0/bookmarks/5", "value": {"guideId": "b"}},
            {"op": "test", "path": "/0/missing", "value": None},
            {"op": "test", "path": "/0/bookmarks", "value": [{"guideId": "a"}]},
        ],
    )
    def test_a_test_that_finds_another_value_or_none_fails(self, operation):
        with pytest.raises(json_patch.PatchTestFailedError):
            _patched(_cases(), operation)

    def test_true_is_not_one(self):
        with pytest.raises(json_patch.PatchTestFailedError):
            _patched([True], {"op": "test", "path": "/0", "value": 1})


class TestRefusing:
    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "add", "path": "0/ref", "value": "W3"},
            {"op": "add", "path": "/0/bookmarks/3", "value": {}},
            {"op": "add", "path": "/0/bookmarks/01", "value": {}},
            {"op": "add", "path": "/5/bookmarks/0", "value": {}},
            {"op": "remove", "path": "/0/bookmarks/2"},
            {"op": "remove", "path": "/0/bookmarks/-"},
            {"op": "remove", "path": "/0/missing"},
            {"op": "remove", "path": ""},
            {"op": "move", "from": "/0", "path": "/0/bookmarks/0"},
            {"op": "add", "path": "/0/ref/x", "value": 1},
            {"op": "add", "path": "/a~2b", "value": 1},
        ],
    )
    def test_an_operation_that_cannot_apply_is_refused(self, operation):
        with pytest.raises(json_patch.PatchRefusedError):
            _patched(_cases(), operation)

    def test_an_operation_json_patch_has_but_these_lists_do_not_take_is_refused(self):
        with pytest.raises(pydantic.ValidationError):
            PATCH.validate_python([{"op": "replace", "path": "/0", "value": 1}])


class TestApplyingWhole:
    def test_the_document_given_is_never_changed(self):
        cases = _cases()

        _patched(cases, {"op": "remove", "path": "/0/bookmarks/0"})

        assert cases == _cases()

    def test_a_failure_part_way_applies_nothing(self):
        cases = _cases()

        with pytest.raises(json_patch.PatchTestFailedError):
            _patched(
                cases,
                {"op": "remove", "path": "/0"},
                {"op": "test", "path": "/0/ref", "value": "W9"},
            )

        assert cases == _cases()
