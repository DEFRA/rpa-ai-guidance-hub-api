"""How like a guide's title is to a title someone gives: the hub's rule for finding
guides by title.

The two are compared word by word: in lower case, without accents, each dash and
punctuation mark a space. A title is like the one given only when they share a run of
three characters along a word, its start padded. How alike they are is the mean of
`rapidfuzz`'s `partial_token_sort_ratio`, which forgives words in another order, and
`partial_ratio`, which favours a title that starts as the one given does. For titles
typed with typos, half words and words in another order, the mean put the guide meant
first where each scorer alone missed some.
"""

from __future__ import annotations

import unicodedata

from rapidfuzz import fuzz


def score(wanted: str, title: str) -> float:
    """How like `title` is to `wanted`, from 0 to 100; 0 for not alike."""
    wanted = _normalised(wanted)
    title = _normalised(title)
    if not _word_trigrams(wanted) & _word_trigrams(title):
        return 0.0
    return (
        fuzz.partial_token_sort_ratio(wanted, title) + fuzz.partial_ratio(wanted, title)
    ) / 2


def _normalised(text: str) -> str:
    characters = [
        character if character.isalnum() else " "
        for character in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(character)
    ]
    return " ".join("".join(characters).split())


def _word_trigrams(normalised: str) -> set[str]:
    found: set[str] = set()
    for word in normalised.split():
        padded = f"  {word} "
        found.update(padded[at : at + 3] for at in range(len(padded) - 2))
    return found
