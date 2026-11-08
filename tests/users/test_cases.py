"""A user's cases and their bookmarks, changed by JSON Patch over the whole structure:
what a patch may make of them, on their own."""

from __future__ import annotations

import datetime as dt
import uuid

import pydantic
import pytest

from app.users import cases, json_patch, models, schemas

A, B = (uuid.uuid7() for _ in range(2))
NOW = dt.datetime(2026, 11, 8, 18, 0, tzinfo=dt.UTC)
EARLIER = dt.datetime(2026, 11, 7, 9, 0, tzinfo=dt.UTC)
KEY, OTHER_KEY = (uuid.uuid4() for _ in range(2))

# Each guide's sections by anchor, as its latest version has them, in its order.
ONE, TWO, OTHER = (uuid.uuid7() for _ in range(3))
SECTIONS = {
    A: models.GuideSections({"1-one": ONE, "2-two": TWO}),
    B: models.GuideSections({"1-other": OTHER}),
}

PATCH = pydantic.TypeAdapter(list[schemas.PatchOperation])


def _stored(*case_list):
    return [case.to_document() for case in case_list]


def _case(ref="W1234567", *bookmarks, case_type="case", key=KEY):
    return models.Case(key=key, type=case_type, ref=ref, bookmarks=tuple(bookmarks))


def _bookmark(guide=A, section_id=ONE, heading="1 One"):
    return models.Bookmark(
        document_id=guide, section_id=section_id, heading=heading, created_at=EARLIER
    )


def _patched(read, *operations, sbis=True):
    return [
        models.Case.from_document(document)
        for document in cases.applied(
            read,
            PATCH.validate_python(list(operations)),
            NOW,
            SECTIONS,
            sbis_allowed=sbis,
        )
    ]


def _new_case(ref, case_type="case", section="1-one", **bookmark):
    return {
        "op": "add",
        "path": "/-",
        "value": {
            "type": case_type,
            "ref": ref,
            "bookmarks": [{"guideId": str(A), "section": section, **bookmark}],
        },
    }


class TestBookmarking:
    def test_a_bookmark_to_a_new_case_makes_the_case_with_a_key_and_when(self):
        [case] = _patched([], _new_case("W1234567", section="2-two", heading="2 Two"))

        assert case.key.version == 4
        assert case == _case(
            "W1234567",
            models.Bookmark(
                document_id=A, section_id=TWO, heading="2 Two", created_at=NOW
            ),
            key=case.key,
        )

    def test_a_section_named_by_its_anchor_is_kept_by_its_id(self):
        [case] = _patched([], _new_case("W1234567", section="2-two"))

        assert [bookmark.section_id for bookmark in case.bookmarks] == [TWO]

    def test_a_patch_reads_each_bookmarked_section_at_its_anchor(self):
        [case] = _patched(
            _stored(_case("W1234567", _bookmark(A, TWO), _bookmark(A, ONE))),
            {"op": "test", "path": "/0/bookmarks/0/section", "value": "2-two"},
            {"op": "remove", "path": "/0/bookmarks/0"},
        )

        assert case.bookmarks == (_bookmark(A, ONE),)

    def test_a_bookmark_added_to_a_case_keeps_the_case_and_its_others_as_they_were(
        self,
    ):
        [case] = _patched(
            _stored(_case("W1234567", _bookmark())),
            {"op": "test", "path": "/0/key", "value": str(KEY)},
            {
                "op": "add",
                "path": "/0/bookmarks/-",
                "value": {"guideId": str(B), "section": "1-other"},
            },
        )

        assert case == _case(
            "W1234567",
            _bookmark(),
            models.Bookmark(
                document_id=B, section_id=OTHER, heading=None, created_at=NOW
            ),
        )

    def test_a_case_id_is_kept_in_upper_case(self):
        [case] = _patched([], _new_case("w1234567"))

        assert case.ref == "W1234567"

    @pytest.mark.parametrize("sbi", ["105000000", "999999999"])
    def test_an_sbi_from_105000000_to_999999999_is_kept(self, sbi):
        [case] = _patched([], _new_case(sbi, case_type="sbi"))

        assert (case.type, case.ref) == ("sbi", sbi)

    def test_an_sbi_already_kept_stays_when_sbis_are_switched_off(self):
        read = _stored(_case("123456789", _bookmark(), case_type="sbi"))

        [case] = _patched(read, {"op": "remove", "path": "/0/bookmarks/0"}, sbis=False)

        assert (case.type, case.ref, case.bookmarks) == ("sbi", "123456789", ())

    def test_removing_a_case_s_last_bookmark_leaves_the_case(self):
        [case] = _patched(
            _stored(_case("W1234567", _bookmark())),
            {"op": "remove", "path": "/0/bookmarks/0"},
        )

        assert case.bookmarks == ()

    def test_several_bookmarks_move_in_one_patch_each_keeping_its_section(self):
        read = _stored(
            _case("W1234567", _bookmark(A, ONE), _bookmark(A, TWO)),
            _case("W7654321", key=OTHER_KEY),
        )

        first, second = _patched(
            read,
            {"op": "test", "path": "/0/bookmarks/0/guideId", "value": str(A)},
            {"op": "move", "from": "/0/bookmarks/0", "path": "/1/bookmarks/0"},
            {"op": "move", "from": "/0/bookmarks/0", "path": "/1/bookmarks/1"},
        )

        assert first.bookmarks == ()
        assert [bookmark.section_id for bookmark in second.bookmarks] == [ONE, TWO]
        assert (first.key, second.key) == (KEY, OTHER_KEY)


class TestRefusing:
    @pytest.mark.parametrize(
        ("case_type", "ref"),
        [
            ("case", "W123456"),
            ("case", "W12345678"),
            ("case", "12345678"),
            ("case", "WW123456"),
            ("case", "W12345 7"),
            ("case", "É1234567"),
            ("sbi", "104999999"),
            ("sbi", "012345678"),
            ("sbi", "1234567890"),
            ("sbi", "0105000000"),
            ("sbi", "A12345678"),
            ("crn", "W1234567"),
        ],
    )
    def test_a_reference_that_is_not_one_is_refused(self, case_type, ref):
        with pytest.raises(cases.InvalidCasesError):
            _patched([], _new_case(ref, case_type=case_type))

    def test_a_new_sbi_is_refused_while_sbis_are_switched_off(self):
        with pytest.raises(cases.InvalidCasesError):
            _patched([], _new_case("123456789", case_type="sbi"), sbis=False)

    def test_a_case_twice_is_refused(self):
        with pytest.raises(cases.InvalidCasesError):
            _patched(_stored(_case("W1234567", _bookmark())), _new_case("w1234567"))

    def test_two_cases_with_one_key_are_refused(self):
        same_key = {
            "op": "add",
            "path": "/-",
            "value": {
                "key": str(KEY),
                "type": "case",
                "ref": "W7654321",
                "bookmarks": [],
            },
        }

        with pytest.raises(cases.InvalidCasesError):
            _patched(_stored(_case("W1234567", _bookmark())), same_key)

    def test_the_same_section_of_a_guide_twice_in_a_case_is_refused(self):
        read = _stored(_case("W1234567", _bookmark(A, ONE)))
        again = {
            "op": "add",
            "path": "/0/bookmarks/-",
            "value": {"guideId": str(A), "section": "1-one"},
        }

        with pytest.raises(cases.InvalidCasesError):
            _patched(read, again)

    @pytest.mark.parametrize("anchor", ["1-other", "9-never"])
    def test_a_section_the_guide_has_not_at_that_anchor_is_refused(self, anchor):
        with pytest.raises(cases.InvalidCasesError):
            _patched([], _new_case("W1234567", section=anchor))

    def test_a_bookmark_of_no_section_is_refused(self):
        whole_guide = {
            "op": "add",
            "path": "/-",
            "value": {
                "type": "case",
                "ref": "W1234567",
                "bookmarks": [{"guideId": str(A)}],
            },
        }

        with pytest.raises(cases.InvalidCasesError):
            _patched([], whole_guide)

    def test_anything_the_structure_does_not_hold_is_refused(self):
        with pytest.raises(cases.InvalidCasesError):
            _patched(
                _stored(_case("W1234567", _bookmark())),
                {"op": "add", "path": "/0/colour", "value": "red"},
            )

    def test_a_failed_test_applies_nothing(self):
        read = _stored(_case("W1234567", _bookmark()))
        stale = PATCH.validate_python(
            [
                {"op": "test", "path": "/0/key", "value": str(OTHER_KEY)},
                {"op": "remove", "path": "/0"},
            ]
        )

        with pytest.raises(json_patch.PatchTestFailedError):
            cases.applied(read, stale, NOW, SECTIONS, sbis_allowed=True)


class TestGuides:
    def test_added_are_the_guides_any_add_puts_in_at_any_depth(self):
        operations = PATCH.validate_python(
            [
                _new_case("W1234567"),
                {
                    "op": "add",
                    "path": "/1/bookmarks/0",
                    "value": {"guideId": str(B), "section": "1-other"},
                },
                {"op": "test", "path": "/0/bookmarks/0/guideId", "value": str(B)},
            ]
        )

        assert cases.guides_added(operations) == {A, B}

    def test_added_refuses_a_guide_id_that_is_not_one(self):
        operations = PATCH.validate_python(
            [
                {
                    "op": "add",
                    "path": "/0/bookmarks/0",
                    "value": {"guideId": "not-a-uuid"},
                }
            ]
        )

        with pytest.raises(cases.InvalidCasesError):
            cases.guides_added(operations)

    def test_in_the_stored_cases_are_every_guide_bookmarked_to_any_case(self):
        read = _stored(
            _case("W1234567", _bookmark(A)),
            _case("W7654321", _bookmark(B, OTHER), key=OTHER_KEY),
        )

        assert cases.guides_in(read) == {A, B}
