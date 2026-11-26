"""Matching a new version's sections with the version before: by the words of their
headings, numbers aside, then by the same words under them; the rest are new.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

from app.guidance import section_matching
from app.guidance.documents import reader
from app.guidance.parsing import anchors

BEFORE = (
    "# Claims guide\n\n"
    "## 1 Introduction\n\nThis guide covers checking a claim before it is paid.\n\n"
    "## 2 Checks\n\nCheck the land use against the options claimed on every parcel.\n\n"
    "## 3 Payment\n\nPay the claim once every check has passed and is recorded.\n\n"
    "## 4 Appeals\n\nA customer may appeal within sixty days of the decision letter.\n"
)


def _matched(before: str, after: str) -> list[tuple[str | None, str]]:
    """Each section of `after`, by its anchor, beside the section of `before` it is."""
    earlier = reader.from_markdown(before).sections
    later = reader.from_markdown(after).sections
    earlier_anchors = anchors.of_document(earlier)
    later_anchors = anchors.of_document(later)
    return [
        (
            None if at is None else earlier_anchors[id(earlier[at])],
            later_anchors[id(section)],
        )
        for section, at in zip(
            later, section_matching.earlier_sections(earlier, later), strict=True
        )
    ]


class TestAVersionUnchanged:
    def test_matches_every_section_with_itself(self):
        assert _matched(BEFORE, BEFORE) == [
            ("1-introduction", "1-introduction"),
            ("2-checks", "2-checks"),
            ("3-payment", "3-payment"),
            ("4-appeals", "4-appeals"),
        ]


class TestASection:
    def test_renumbered_is_matched_by_its_heading_words(self):
        after = (
            BEFORE.replace(
                "## 2 Checks", "## 2 Eligibility\n\nWho may claim.\n\n## 3 Checks"
            )
            .replace("## 3 Payment", "## 4 Payment")
            .replace("## 4 Appeals", "## 5 Appeals")
        )

        assert _matched(BEFORE, after) == [
            ("1-introduction", "1-introduction"),
            (None, "2-eligibility"),
            ("2-checks", "3-checks"),
            ("3-payment", "4-payment"),
            ("4-appeals", "5-appeals"),
        ]

    def test_reworded_with_the_same_words_under_it_is_matched_by_them(self):
        after = BEFORE.replace("## 3 Payment", "## 3 Paying the claim")

        assert ("3-payment", "3-paying-the-claim") in _matched(BEFORE, after)

    def test_reworded_with_other_words_under_it_is_new(self):
        after = BEFORE.replace(
            "## 4 Appeals\n\nA customer may appeal within sixty days of the decision letter.\n",
            "## 4 Records\n\nKeep the evidence for seven years in the case file.\n",
        )

        assert (None, "4-records") in _matched(BEFORE, after)

    def test_headed_alike_twice_is_matched_in_order(self):
        before = "## Notes\n\nFirst note.\n\n## Notes\n\nSecond note.\n"
        after = "## Notes\n\nFirst note.\n\n## Notes\n\nSecond note, changed.\n"

        assert _matched(before, after) == [("notes", "notes"), ("notes-1", "notes-1")]


class TestTheWordsUnderAHeading:
    def test_match_whatever_spaces_and_lines_are_between_them(self):
        after = BEFORE.replace("## 3 Payment", "## 3 Paying the claim").replace(
            "once every check has passed", "once  every check\nhas passed"
        )

        assert ("3-payment", "3-paying-the-claim") in _matched(BEFORE, after)

    def test_match_nothing_when_there_are_none(self):
        before = "## 1 Forms\n\n## 2 Notes\n\nA note.\n"
        after = "## 1 Paper forms\n\n## 2 Notes\n\nA note.\n"

        assert _matched(before, after) == [
            (None, "1-paper-forms"),
            ("2-notes", "2-notes"),
        ]

    def test_match_nothing_when_two_sections_on_one_side_have_them(self):
        before = "## 1 Forms\n\nSee the list.\n\n## 2 Letters\n\nSee the list.\n"
        after = (
            "## 1 Paper forms\n\nSee the list.\n\n## 2 Letters sent\n\nSee the list.\n"
        )

        assert _matched(before, after) == [
            (None, "1-paper-forms"),
            (None, "2-letters-sent"),
        ]
