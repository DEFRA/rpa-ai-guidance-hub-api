"""The two ink colours a guidance document writes in, and the Markdown that carries them.

Markdown has no syntax for colour, so a coloured run is written as a Pandoc-style
bracketed span - `[text]{.red}`. Both ends of that form live here, as they do for
images: the name is produced in one file and spelled in one file, so the rule and the
syntax cannot drift apart.

Two things make this the right shape rather than an inline `<span style>`:

- A style attribute cannot be written back out. The editor's serialiser has no rule for
  a colour mark, so the first save drops it silently - and it would not render anyway
  under a `style-src 'self'` policy, which a style attribute falls under.
- HTML is not a container for Markdown. An inline span is handed to an HTML parse, so
  emphasis inside one stops being emphasis and comes back as literal asterisks, which
  the *next* save then stores. A bracketed span is content, and its contents are
  re-tokenised as Markdown, so a mark inside a coloured run survives every leg.

What is stored is the name, never a hex and never a CSS class, so the palette can be
restyled without rewriting a single document.
"""

from __future__ import annotations

# The two names. A document's colour is an instruction to the person working the case -
# fill this in, or choose one of these - rather than decoration, so the vocabulary is
# the small closed set the convention actually uses.
RED = "red"
BLUE = "blue"

# A hex colour, which is the only thing worth measuring. Word also writes the token
# "auto", meaning "whatever contrasts with the background": a deferral, not a colour.
_HEX_LENGTH = 6
_CHANNEL = 2


def name_for(value: str | None) -> str:
    """The name of the colour Word wrote, or "" where it named no colour at all.

    Every colour is matched to the nearer of red and blue, so a shade an author reached
    for by hand is read as the intent it approximates rather than kept as a stray. The
    comparison is just red channel against blue channel, and that is not an
    approximation of nearest-of-two but exactly equal to it: expand the squared
    distances to pure red and pure blue and every term cancels but 510*(blue - red), so
    the larger channel always names the nearer colour.

    Equal channels mean the colour lies on the plane between the two and names neither.
    That is one rule covering what would otherwise be a list of exceptions - black,
    every grey, white, and the greens and magentas nothing here would know what to do
    with - and it is why the default colour needs no naming: Word spells the default out
    explicitly rather than leaving it unsaid, on most of the coloured runs in a
    document, and it is achromatic every time.
    """
    channels = _channels(value)
    if channels is None:
        return ""

    red, blue = channels
    if red == blue:
        return ""
    return RED if red > blue else BLUE


def marked_up(text: str, name: str) -> str:
    """One coloured span, as the bracketed-span syntax spells it.

    The text is already escaped by the time it arrives, which is what the form needs:
    a bracket the author typed has to be `\\[` here, or it would close the span early
    and the colour would be dropped by whatever reads it back.
    """
    return f"[{text}]{{.{name}}}"


def _channels(value: str | None) -> tuple[int, int] | None:
    """The red and blue channels of a hex colour, or None for anything else.

    Green is read past rather than parsed: it cannot affect which of red and blue is
    nearer, so it is not information this question has any use for.
    """
    if value is None or len(value) != _HEX_LENGTH:
        return None

    try:
        red = int(value[:_CHANNEL], 16)
        blue = int(value[-_CHANNEL:], 16)
    except ValueError:
        return None

    return red, blue
