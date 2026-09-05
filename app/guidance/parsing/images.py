"""The pictures a document draws, and the two-step way they are named.

An image reaches the Markdown in two moves, because the two things needed to write it
are known in different places. `inline` meets the picture while rendering a run and
knows only which relationship it points at, so it writes `![](rId7)`. The parser knows
which section the block landed in, and only a section can say what the picture should
be called - so it is `resolved` afterwards, against the section, into
`![](3.1_img_2.png)`.

Keeping both ends here is the point of the module: the form is written once and read
once, in one file, so the two cannot drift apart. It is the same late binding
`models.py` already does for the image prefix and for cross-references, one step
earlier - and it is what lets `inline`, `lists` and `tables` stay entirely ignorant of
images.

The alt text is deliberately empty. Word writes a `wp:docPr/@descr` of its own accord,
describing what it thinks a picture shows, and nothing in the file distinguishes that
from a description a person wrote - so emitting it would publish a machine's guess as
authored text, and to a screen reader the two are indistinguishable. Whoever edits the
guidance can put a real description in; saying nothing is the honest placeholder until
they do.
"""

from __future__ import annotations

import re
from typing import Any

from docx.oxml.ns import qn

from app.guidance.parsing import models

# What `placeholder` writes and `resolved` reads back. The only `![]()` in a block is
# one this module put there, so the pattern needs no guard against document text: the
# escaping in `inline` means text the author typed cannot produce brackets bare.
_PLACEHOLDER = re.compile(r"!\[\]\(([^)\s]+)\)")

# The generated name, e.g. "3.1_img_2.png": the section it appears in, its position
# within that section, and the extension of the part it came from. Generated rather
# than taken from word/media so that a name says where the picture belongs, and cannot
# collide with anything the document itself says.
_NAME = "{number}_img_{position}.{extension}"


def embedded_in(run: Any) -> str:
    """The relationship id of the picture this run draws, or "" for any other run.

    Only the run's own w:drawing children are looked at, never its descendants, and
    that is what keeps a text box out: Word wraps a text box's drawing in an
    mc:AlternateContent, so it is a grandchild and `textboxes` deals with it. A
    drawing holding a shape rather than a picture has no a:blip and is no image here.
    """
    for drawing in run.findall(qn("w:drawing")):
        for blip in drawing.iter(qn("a:blip")):
            embed = blip.get(qn("r:embed"))
            if embed:
                return str(embed)
    return ""


def placeholder(embed: str) -> str:
    """A picture as `inline` can write it, knowing only its relationship."""
    return f"![]({embed})"


def resolved(block: str, section: models.MarkdownSection, part: Any) -> str:
    """Name the pictures in a block, keeping them on the section as they are met.

    Called once per section, on content that has already been filed - so a picture in
    a block ahead of the first heading is dropped with the block, and its bytes are
    never read. `part` is the document part the relationships belong to.
    """

    def named(match: re.Match[str]) -> str:
        image = _extracted(match.group(1), section, part)
        section.images.append(image)
        return f"![]({image.name})"

    return _PLACEHOLDER.sub(named, block)


def _extracted(embed: str, section: models.MarkdownSection, part: Any) -> models.Image:
    """One picture, named for where it sits and carrying the bytes of its part."""
    source = part.related_parts[embed]
    name = _NAME.format(
        number=section.number,
        position=len(section.images) + 1,
        extension=source.partname.ext,
    )
    return models.Image(
        name=name, data=source.blob, content_type=str(source.content_type)
    )


def name_all(sections: list[models.MarkdownSection], part: Any) -> None:
    """Name the pictures of every section, in document order.

    Numbering is per section and starts at 1, so it depends on the sections being
    resolved in the order they appear - which is the order they are built in.
    """
    for section in sections:
        section.content = resolved(section.content, section, part)
