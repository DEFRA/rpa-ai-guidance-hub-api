"""How like a guide's title is to a title someone gives: the hub's rule for finding
guides by title.

The two are compared word by word: in lower case, without accents, each dash and
punctuation mark a space. How alike they are is the mean of `rapidfuzz`'s
`partial_token_sort_ratio`, which forgives words in another order, and
`partial_ratio`, which favours a title that starts as the one given does. For titles
typed with typos, half words and words in another order, the mean put the guide meant
first where each scorer alone missed some.

A title is like the one given only from a score of `LEAST_ALIKE`. Over guide titles,
each one meant scored 73 or more, and words in none of the titles 54 or less. A short
word can still score more against part of a longer one: "tree" scored 75.
"""

from __future__ import annotations

import unicodedata

from rapidfuzz import fuzz

LEAST_ALIKE: int = 60


def score(wanted: str, title: str) -> float:
    """How like `title` is to `wanted`, from 0 to 100; 0 for not alike."""
    wanted = _normalised(wanted)
    title = _normalised(title)
    mean = (
        fuzz.partial_token_sort_ratio(wanted, title) + fuzz.partial_ratio(wanted, title)
    ) / 2
    return mean if mean >= LEAST_ALIKE else 0.0


def _normalised(text: str) -> str:
    characters = [
        character if character.isalnum() else " "
        for character in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(character)
    ]
    return " ".join("".join(characters).split())
