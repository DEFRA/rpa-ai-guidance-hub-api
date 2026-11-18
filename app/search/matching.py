"""The keyword rule the hub's own search matches words by.

**Matching is Word's Find:** anywhere in the text, in any case, so "pay" finds
"repayment"; several words match as that phrase, with any space between them. A text
holds one block to a line (`reading`), and a phrase never runs from one block into the
next. **A query of one or two characters is short,** and matches whole words only, so
"CS" finds CS and not "docs".
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
    order, each counted once; a short term's only where it is a whole word."""
    words = term.split()
    if not words:
        return []

    anywhere = len(term) >= _SHORTEST_PART_OF_A_WORD
    lower = _lower(text)
    matches: list[tuple[int, int]] = []
    at = lower.find(words[0])
    while at != -1:
        end = _phrase_end(lower, at, words)
        matched = end != -1 and (anywhere or _is_whole_word(text, at, end))
        if matched:
            matches.append((at, end))
        at = lower.find(words[0], end if matched else at + 1)
    return matches


def _is_whole_word(text: str, start: int, end: int) -> bool:
    """Whether no letter or digit runs on from either end of `text[start:end]`."""
    runs_on_before = start > 0 and text[start - 1].isalnum()
    runs_on_after = end < len(text) and text[end].isalnum()
    return not (runs_on_before or runs_on_after)


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
