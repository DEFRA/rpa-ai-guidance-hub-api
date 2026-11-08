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


async def _sections_of(guides):
    """The sections of only the guides asked for, as the router reads them, so a
    guide the patch names but the caller is not asked about has none."""
    return {guide: SECTIONS[guide] for guide in guides if guide in SECTIONS}


async def _patched(read, *operations, sbis=True):
    return [
        models.Case.from_document(document)
        for document in await cases.applied(
            read,
            PATCH.validate_python(list(operations)),
            NOW,
            _sections_of,
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
    async def test_a_bookmark_to_a_new_case_makes_the_case_with_a_key_and_when(self):
        [case] = await _patched(
            [], _new_case("W1234567", section="2-two", heading="2 Two")
        )

        assert case.key.version == 4
        assert case == _case(
            "W1234567",
            models.Bookmark(
                document_id=A, section_id=TWO, heading="2 Two", created_at=NOW
            ),
            key=case.key,
        )

    async def test_a_section_named_by_its_anchor_is_kept_by_its_id(self):
        [case] = await _patched([], _new_case("W1234567", section="2-two"))

        assert [bookmark.section_id for bookmark in case.bookmarks] == [TWO]

    async def test_a_patch_reads_each_bookmarked_section_at_its_anchor(self):
        [case] = await _patched(
            _stored(_case("W1234567", _bookmark(A, TWO), _bookmark(A, ONE))),
            {"op": "test", "path": "/0/bookmarks/0/section", "value": "2-two"},
            {"op": "remove", "path": "/0/bookmarks/0"},
        )

        assert case.bookmarks == (_bookmark(A, ONE),)

    async def test_a_bookmark_added_to_a_case_keeps_the_case_and_its_others_as_they_were(
        self,
    ):
        [case] = await _patched(
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

    async def test_a_case_id_is_kept_in_upper_case(self):
        [case] = await _patched([], _new_case("w1234567"))

        assert case.ref == "W1234567"

    @pytest.mark.parametrize("sbi", ["105000000", "999999999"])
    async def test_an_sbi_from_105000000_to_999999999_is_kept(self, sbi):
        [case] = await _patched([], _new_case(sbi, case_type="sbi"))

        assert (case.type, case.ref) == ("sbi", sbi)

    async def test_an_sbi_already_kept_stays_when_sbis_are_switched_off(self):
        read = _stored(_case("123456789", _bookmark(), case_type="sbi"))

        [case] = await _patched(
            read, {"op": "remove", "path": "/0/bookmarks/0"}, sbis=False
        )

        assert (case.type, case.ref, case.bookmarks) == ("sbi", "123456789", ())

    async def test_removing_a_case_s_last_bookmark_leaves_the_case(self):
        [case] = await _patched(
            _stored(_case("W1234567", _bookmark())),
            {"op": "remove", "path": "/0/bookmarks/0"},
        )

        assert case.bookmarks == ()

    async def test_several_bookmarks_move_in_one_patch_each_keeping_its_section(self):
        read = _stored(
            _case("W1234567", _bookmark(A, ONE), _bookmark(A, TWO)),
            _case("W7654321", key=OTHER_KEY),
        )

        first, second = await _patched(
            read,
            {"op": "test", "path": "/0/bookmarks/0/guideId", "value": str(A)},
            {"op": "move", "from": "/0/bookmarks/0", "path": "/1/bookmarks/0"},
            {"op": "move", "from": "/0/bookmarks/0", "path": "/1/bookmarks/1"},
        )

        assert first.bookmarks == ()
        assert [bookmark.section_id for bookmark in second.bookmarks] == [ONE, TWO]
        assert (first.key, second.key) == (KEY, OTHER_KEY)

    async def test_a_guide_named_only_by_a_changed_member_is_read_for_its_sections(
        self,
    ):
        read = _stored(_case("W1234567", _bookmark(A, ONE)))

        [case] = await _patched(
            read,
            {"op": "remove", "path": "/0/bookmarks/0/sectionId"},
            {"op": "add", "path": "/0/bookmarks/0/guideId", "value": str(B)},
            {"op": "add", "path": "/0/bookmarks/0/section", "value": "1-other"},
        )

        assert [(b.document_id, b.section_id) for b in case.bookmarks] == [(B, OTHER)]

    async def test_a_guide_named_as_guide_id_is_read_for_its_sections(self):
        [case] = await _patched(
            [],
            {
                "op": "add",
                "path": "/-",
                "value": {
                    "type": "case",
                    "ref": "W1234567",
                    "bookmarks": [{"guide_id": str(B), "section": "1-other"}],
                },
            },
        )

        assert [(b.document_id, b.section_id) for b in case.bookmarks] == [(B, OTHER)]

    async def test_a_bookmark_kept_stays_when_its_guide_no_longer_exists(self):
        gone = uuid.uuid7()
        read = _stored(
            _case("W1234567", _bookmark(gone, ONE)),
            _case("W7654321", key=OTHER_KEY),
        )

        _, case = await _patched(
            read, {"op": "move", "from": "/0/bookmarks/0", "path": "/1/bookmarks/0"}
        )

        assert case.bookmarks == (_bookmark(gone, ONE),)


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
    async def test_a_reference_that_is_not_one_is_refused(self, case_type, ref):
        operation = _new_case(ref, case_type=case_type)

        with pytest.raises(cases.InvalidCasesError):
            await _patched([], operation)

    async def test_a_new_sbi_is_refused_while_sbis_are_switched_off(self):
        operation = _new_case("123456789", case_type="sbi")

        with pytest.raises(cases.InvalidCasesError):
            await _patched([], operation, sbis=False)

    async def test_a_case_made_an_sbi_is_refused_while_sbis_are_switched_off(self):
        read = _stored(_case("W1234567", _bookmark()))
        made_an_sbi = [
            {"op": "add", "path": "/0/type", "value": "sbi"},
            {"op": "add", "path": "/0/ref", "value": "123456789"},
        ]

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, *made_an_sbi, sbis=False)

    async def test_a_kept_sbi_given_another_reference_is_refused_while_sbis_are_off(
        self,
    ):
        read = _stored(_case("123456789", _bookmark(), case_type="sbi"))
        operation = {"op": "add", "path": "/0/ref", "value": "987654321"}

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, operation, sbis=False)

    async def test_a_case_twice_is_refused(self):
        read = _stored(_case("W1234567", _bookmark()))
        operation = _new_case("w1234567")

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, operation)

    async def test_two_cases_with_one_key_are_refused(self):
        read = _stored(_case("W1234567", _bookmark()))
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
            await _patched(read, same_key)

    async def test_the_same_section_of_a_guide_twice_in_a_case_is_refused(self):
        read = _stored(_case("W1234567", _bookmark(A, ONE)))
        again = {
            "op": "add",
            "path": "/0/bookmarks/-",
            "value": {"guideId": str(A), "section": "1-one"},
        }

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, again)

    @pytest.mark.parametrize("anchor", ["1-other", "9-never"])
    async def test_a_section_the_guide_has_not_at_that_anchor_is_refused(self, anchor):
        operation = _new_case("W1234567", section=anchor)

        with pytest.raises(cases.InvalidCasesError):
            await _patched([], operation)

    async def test_a_bookmark_of_no_section_is_refused(self):
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
            await _patched([], whole_guide)

    async def test_anything_the_structure_does_not_hold_is_refused(self):
        read = _stored(_case("W1234567", _bookmark()))
        operation = {"op": "add", "path": "/0/colour", "value": "red"}

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, operation)

    @pytest.mark.parametrize("guide", [uuid.uuid7(), "not-a-guide-id"])
    async def test_a_bookmark_moved_to_a_guide_there_is_not_is_refused(self, guide):
        read = _stored(_case("W1234567", _bookmark(A, ONE)))
        operation = {"op": "add", "path": "/0/bookmarks/0/guideId", "value": str(guide)}

        with pytest.raises(cases.InvalidCasesError):
            await _patched(read, operation)

    async def test_a_new_bookmark_of_a_guide_there_is_not_is_refused(self):
        operation = _new_case("W1234567", section=None, sectionId=str(ONE))
        operation["value"]["bookmarks"][0]["guideId"] = str(uuid.uuid7())

        with pytest.raises(cases.InvalidCasesError):
            await _patched([], operation)

    async def test_a_failed_test_applies_nothing(self):
        read = _stored(_case("W1234567", _bookmark()))
        stale = PATCH.validate_python(
            [
                {"op": "test", "path": "/0/key", "value": str(OTHER_KEY)},
                {"op": "remove", "path": "/0"},
            ]
        )

        with pytest.raises(json_patch.PatchTestFailedError):
            await cases.applied(read, stale, NOW, _sections_of, sbis_allowed=True)
