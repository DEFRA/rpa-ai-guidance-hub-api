"""The anchor a heading renders with.

These are the rules a stored document's internal links stand on: the writer builds a
link target with them and the reader recognises one with them, so a disagreement here
is a document that cannot be read back.

All fixture text is invented, as everywhere in this package.
"""

from app.guidance.parsing import anchors, models


class TestSlugify:
    def test_a_heading_becomes_its_words_in_lower_case(self):
        assert anchors.slugify("Evidence Required") == "evidence-required"

    def test_the_dot_in_a_section_number_is_dropped_not_replaced(self):
        """This is the whole reason a number cannot be the anchor.

        A renderer builds `#31-overview` from `### 3.1 Overview`, so a link written
        to `#3.1` points at nothing whatever - which is what the stored document
        used to carry.
        """
        assert anchors.slugify("3.1 Overview") == "31-overview"

    def test_punctuation_goes_and_hyphens_stay(self):
        """A hyphen is already what a space becomes, so it survives as itself and a
        dash between words arrives as a run of them."""
        assert anchors.slugify("Annex A - Case types") == "annex-a---case-types"
        assert anchors.slugify("What, exactly?") == "what-exactly"


class TestAnchorsOfADocument:
    def test_a_numbered_heading_carries_its_number_into_its_anchor(self):
        section = models.MarkdownSection(heading="Overview", ordinal=3)

        assert anchors.of_document([section])[id(section)] == "3-overview"

    def test_two_sections_headed_alike_cannot_collide_while_they_are_numbered(self):
        """The number is part of what a heading prints, so it is part of the slug.
        Nothing has to disambiguate these."""
        first = models.MarkdownSection(heading="Overview", ordinal=1)
        second = models.MarkdownSection(heading="Overview", ordinal=2)

        of_section = anchors.of_document([first, second])

        assert of_section[id(first)] == "1-overview"
        assert of_section[id(second)] == "2-overview"

    def test_two_appendices_headed_alike_are_told_apart_by_a_suffix(self):
        """An appendix prints its heading alone, so it is the one heading that can
        collide - and a renderer suffixes the second, so this does too."""
        first = models.MarkdownSection(heading="Case types", ordinal=1, appendix=True)
        second = models.MarkdownSection(heading="Case types", ordinal=2, appendix=True)

        of_section = anchors.of_document([first, second])

        assert of_section[id(first)] == "case-types"
        assert of_section[id(second)] == "case-types-1"

    def test_two_sections_that_compare_equal_still_get_an_anchor_each(self):
        """Keyed by identity, not by value: two equal sections render two headings,
        and a document with one anchor for both would link half of them nowhere."""
        first = models.MarkdownSection(heading="Overview", ordinal=1)
        second = models.MarkdownSection(heading="Overview", ordinal=1)

        assert first == second
        assert len(anchors.of_document([first, second])) == 2


class TestTheMapADocumentHandsItsSections:
    def test_a_bookmark_resolves_to_the_anchor_of_the_section_it_marks(self):
        payment = models.MarkdownSection(heading="Payment", ordinal=4)
        document = models.MarkdownDocument(
            sections=[payment], bookmarks={"_Payment": payment}
        )

        assert document.anchors() == {"_Payment": "4-payment"}

    def test_a_bookmark_on_a_section_the_document_does_not_carry_is_dropped(self):
        """Rendering it would write a link to an anchor no heading in the file
        prints, which is exactly what a self-contained document must not hold."""
        elsewhere = models.MarkdownSection(heading="Payment", ordinal=4)
        section = models.MarkdownSection(heading="Applying", ordinal=1)
        document = models.MarkdownDocument(
            sections=[section], bookmarks={"_Payment": elsewhere}
        )

        assert document.anchors() == {}
