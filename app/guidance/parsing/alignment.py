"""Line a rendered document's pipe tables up into the editor's canonical shape.

This is a step of rendering rather than part of writing a table, and it has to be. A
cell's final width is not known when the table is built: a cross-reference is still
the bookmark Word named, and an image is still a bare filename, and both are rewritten
to something of a different length once the document can say what they point at.
Aligning after those holes are filled is the only place the answer is right.

Doing it at all is what makes a converted document a fixed point of the editor. The
editor rewrites every table it saves into this shape, so a table written any other way
comes back changed by the first save a person makes - which buries the losses actually
worth looking at under a diff of realigned pipes, and churns stored content the first
time anyone opens it.

It reads Markdown rather than anything of Word's, which is why it is not part of
`tables`: that module knows what a cell says, and this one only knows how wide it is.
Keeping them apart is also what keeps `models` from importing its way in a circle.
"""

from __future__ import annotations

import re

# A line that could be a row of a pipe table, and the one under a header that says the
# rows around it really are one.
_PIPE_ROW = re.compile(r"^\|.*\|$")
_DELIMITER_ROW = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")

# The narrowest a column is written, whatever it holds: three is the shortest run of
# dashes GFM accepts under a header, so a narrower column could not be delimited.
_MINIMUM_WIDTH = 3


def aligned(markdown: str) -> str:
    """Rendered Markdown with every pipe table in the editor's canonical shape."""
    lines = markdown.split("\n")
    rendered: list[str] = []

    start = 0
    while start < len(lines):
        end = _table_end(lines, start)
        if end == start:
            rendered.append(lines[start])
            start += 1
            continue

        rendered.extend(_aligned_table(lines[start:end]))
        start = end

    return "\n".join(rendered)


def _table_end(lines: list[str], start: int) -> int:
    """Where the table opening at `start` ends, or `start` where none opens there.

    A row alone is not a table: a paragraph can begin with a pipe, and only the
    delimiter under the first row makes what follows a table. Requiring it is what
    keeps this off prose.
    """
    if not _PIPE_ROW.match(lines[start]):
        return start
    if start + 1 >= len(lines) or not _DELIMITER_ROW.match(lines[start + 1]):
        return start

    end = start + 2
    while end < len(lines) and _PIPE_ROW.match(lines[end]):
        end += 1
    return end


def _aligned_table(lines: list[str]) -> list[str]:
    """One table's lines, padded out so its columns line up."""
    header, _, *body = (_split_row(line) for line in lines)
    columns = max(len(row) for row in (header, *body))
    grid = [row + [""] * (columns - len(row)) for row in (header, *body)]
    widths = [
        max([_MINIMUM_WIDTH, *(len(row[column]) for row in grid)])
        for column in range(columns)
    ]

    padded = [_padded_row(row, widths) for row in grid]
    return [padded[0], _padded_delimiter(widths), *padded[1:]]


def _split_row(line: str) -> list[str]:
    """A pipe row's cells, split on the pipes that end a cell and no others.

    A pipe the document means as text is written escaped, so the backslash before it
    is what says this one is content rather than a column boundary.
    """
    cells: list[str] = []
    cell: list[str] = []
    index = 0

    while index < len(line):
        character = line[index]
        if character == "\\" and index + 1 < len(line):
            cell.append(line[index : index + 2])
            index += 2
            continue
        if character == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(character)
        index += 1

    # The first pipe opens the row rather than ending a cell, so what it closed is
    # the empty stretch before it.
    return cells[1:]


def _padded_row(cells: list[str], widths: list[int]) -> str:
    """One row with each cell padded out to its column."""
    padded = (cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
    return "| " + " | ".join(padded) + " |"


def _padded_delimiter(widths: list[int]) -> str:
    """The delimiter row, each run as wide as the column it sits under."""
    return "| " + " | ".join("-" * width for width in widths) + " |"
