"""How the hub writes a link's destination in Markdown, for the converter and for any
code that must find or write a link as the converter wrote it."""

from __future__ import annotations


def written(target: str) -> str:
    """A link target, wrapped in <> only where bare would not parse.

    Wrapping every target holding a parenthesis would be the easier rule and is the
    wrong one, because the editor writes a balanced target bare: a document written
    the cautious way is rewritten by the first save, and a table holding such a link
    is re-measured and re-padded whole. Asking the real question - would this be
    misread bare? - agrees with the editor without conceding anything, since the
    targets it declines to wrap are exactly the ones that do not need it.
    """
    return f"<{target}>" if _needs_brackets(target) else target


def _needs_brackets(target: str) -> bool:
    """Whether a bare destination would be read as something other than itself."""
    if any(character.isspace() for character in target):
        return True

    depth = 0
    for character in target:
        depth += (character == "(") - (character == ")")
        if depth < 0:
            # A close before its open ends the destination early, taking the rest
            # of the address out of the link with it.
            return True
    return depth != 0
