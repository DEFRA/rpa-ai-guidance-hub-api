"""The keyword rule the hub's own search matches words by.

**Matching is Word's Find:** anywhere in the text, in any case, so "pay" finds
"repayment"; several words match as that phrase, with any space between them. A text
holds one block to a line (`reading`), and a phrase never runs from one block into the
next. **A query of one or two characters is short,** and matches whole words only, so
"CS" finds CS and not "docs". **A word with a digit is a code,** such as a section
number or an option code: at either end of a query, it matches only where no letter or
digit runs on from it, so "1.1" finds neither "11.1" nor "1.12", and "BE3" not "BE31",
as the search service matches it.
"""

from __future__ import annotations

# What divides a text's blocks: never a space between a phrase's words.
BLOCK_BREAK = "\n"

# A term shorter than this matches whole words only.
_SHORTEST_PART_OF_A_WORD = 3


def term_of(query: str) -> str:
    """What a query looks for: its words in lower case, one space between, lowered
    as a text is (`_lower`), so that the same words always match."""
    return " ".join(_lower(query).split())


def matches_in(text: str, term: str) -> list[tuple[int, int]]:
    """Every match of a term (as `term_of` gives it) in a text, as `(start, end)` in
    order, each counted once; a short term's only where it is a whole word, and a code
    at either end of a term only where it is whole there."""
    words = term.split()
    if not words:
        return []

    # The edges of a match that no letter or digit may run on from: both, for a short
    # term; else the edge of a code that has a letter or digit there.
    short = len(term) < _SHORTEST_PART_OF_A_WORD
    whole_before = short or (_is_code(words[0]) and words[0][0].isalnum())
    whole_after = short or (_is_code(words[-1]) and words[-1][-1].isalnum())
    lower = _lower(text)
    matches: list[tuple[int, int]] = []
    at = lower.find(words[0])
    while at != -1:
        end = _phrase_end(lower, at, words)
        matched = (
            end != -1
            and not (whole_before and _runs_on_before(text, at))
            and not (whole_after and _runs_on_after(text, end))
        )
        if matched:
            matches.append((at, end))
        at = lower.find(words[0], end if matched else at + 1)
    return matches


def _is_code(word: str) -> bool:
    """A word with a digit in it, such as a section number or an option code."""
    return any(character.isdigit() for character in word)


def _runs_on_before(text: str, start: int) -> bool:
    """Whether a letter or digit comes just before `start`."""
    return start > 0 and text[start - 1].isalnum()


def _runs_on_after(text: str, end: int) -> bool:
    """Whether a letter or digit comes at `end`."""
    return end < len(text) and text[end].isalnum()


def _phrase_end(lower: str, at: int, words: list[str]) -> int:
    """Where the words end if they come in order from `at`, with space between them;
    -1 if they do not."""
    position = at
    for index, word in enumerate(words):
        if index > 0:
            if not _is_space(lower, position):
                return -1
            while _is_space(lower, position):
                position += 1
        if not lower.startswith(word, position):
            return -1
        position += len(word)
    return position


def _is_space(text: str, at: int) -> bool:
    return at < len(text) and text[at].isspace() and text[at] != BLOCK_BREAK


def _lower(text: str) -> str:
    """Text in one case, character for character, so that a match's place is the
    same in both: see `_folded`."""
    return "".join(_folded(character) for character in text)


def _folded(character: str) -> str:
    """A character in one case: folded, so that `ς` and `Σ` are both `σ`; where that
    would be longer, lowered, so that `ẞ` is `ß`; else as it is."""
    folded = character.casefold()
    if len(folded) == 1:
        return folded
    lowered = character.lower()
    return lowered if len(lowered) == 1 else character
