"""The keyword rule the hub's own search matches words by.

All fixture text is invented.
"""

from __future__ import annotations

from app.search import matching


class TestAQuery:
    def test_looks_for_its_words_in_lower_case_one_space_between(self):
        assert matching.term_of("  Single \t PAYMENT\n") == "single payment"


class TestAQueryInAnyCase:
    def test_finds_words_whose_lower_case_is_longer(self):
        assert matching.matches_in("İstanbul office", matching.term_of("İstanbul")) == [
            (0, 8)
        ]

    def test_finds_words_whose_lower_case_depends_on_the_letters_round_them(self):
        assert matching.matches_in("ΟΣ claims", matching.term_of("ΟΣ")) == [(0, 2)]

    def test_finds_a_final_sigma_in_either_case(self):
        assert matching.matches_in("ΟΣ claims", matching.term_of("ος")) == [(0, 2)]

    def test_finds_a_sharp_s_in_either_case(self):
        assert (
            matching.matches_in("STRAẞE", matching.term_of("Straße")),
            matching.matches_in("Straße", matching.term_of("STRAẞE")),
        ) == ([(0, 6)], [(0, 6)])


class TestMatching:
    def test_finds_a_word_anywhere_in_a_word_in_any_case(self):
        assert matching.matches_in("Repayment, then Payment", "pay") == [
            (2, 5),
            (16, 19),
        ]

    def test_finds_several_words_as_that_phrase_with_any_space_between(self):
        assert matching.matches_in("a single  payment", "single payment") == [(2, 17)]

    def test_finds_a_phrase_only_with_its_words_in_order(self):
        assert matching.matches_in("payment single", "single payment") == []

    def test_never_runs_a_phrase_from_one_block_into_the_next(self):
        assert matching.matches_in("a single\npayment", "single payment") == []

    def test_counts_each_match_once_without_overlapping(self):
        assert matching.matches_in("aaaaaaa", "aaa") == [(0, 3), (3, 6)]

    def test_finds_nothing_for_no_words(self):
        assert matching.matches_in("anything", "") == []

    def test_finds_two_characters_only_where_they_are_a_whole_word(self):
        assert matching.matches_in("CS docs (cs) CSV, CS-MA", "cs") == [
            (0, 2),
            (9, 11),
            (18, 20),
        ]

    def test_finds_one_character_only_where_it_is_a_whole_word(self):
        assert matching.matches_in("a cat, a", "a") == [(0, 1), (7, 8)]

    def test_takes_a_block_break_as_the_edge_of_a_word(self):
        assert matching.matches_in("pay\ncs", "cs") == [(4, 6)]

    def test_places_a_match_where_the_text_has_it_after_a_letter_lower_case_is_longer(
        self,
    ):
        assert matching.matches_in("İ pay", "pay") == [(2, 5)]
